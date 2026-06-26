# tool-pipeline-run Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Create a tool module that lets an interactive Amplifier agent invoke DOT graph pipelines at runtime via a `run_pipeline` tool call.

**Architecture:** A new Amplifier tool module (`tool-pipeline-run`) that follows the `tool-recipes` pattern. When the LLM calls `run_pipeline`, the tool resolves the DOT source (file path with `@mention` support or inline string), validates that all required LLM providers are available, then spawns a child session running the `attractor-pipeline-runner` agent (which uses `loop-pipeline` as its orchestrator). The tool blocks until the pipeline completes and returns a structured `ToolResult`. Progress is reported via `DisplaySystem` side-channel messages and hook events.

**Tech Stack:** Python 3.11+, amplifier-core (`ToolResult`, `ModuleCoordinator`), amplifier-module-loop-pipeline (DOT parsing, stylesheet parsing), pytest + pytest-asyncio for tests.

---

## Dependency Map

```
Task 1  Module scaffolding (no deps)
Task 2  PipelineRunTool class skeleton + input validation (depends on Task 1)
Task 3  DOT resolution: file path, inline, @mention (depends on Task 2)
Task 4  Provider validation (depends on Task 3)
Task 5  Spawn execution + ToolResult (depends on Task 4)
Task 6  Progress reporting via DisplaySystem + hook events (depends on Task 5)
Task 7  pipeline-runner agent definition (depends on Task 1)
Task 8  Bundle wiring: context + behavior + bundle entry point (depends on Task 7)
Task 9  Integration test script (depends on all)
```

---

## Task 1: Create Module Scaffolding

**Files:**
- Create: `modules/tool-pipeline-run/pyproject.toml`
- Create: `modules/tool-pipeline-run/amplifier_module_tool_pipeline_run/__init__.py`
- Create: `modules/tool-pipeline-run/tests/__init__.py`
- Create: `modules/tool-pipeline-run/tests/test_pipeline_run.py`

**Depends on:** Nothing
**Effort:** ~2 minutes

### Step 1: Create `pyproject.toml`

```toml
[project]
name = "amplifier-module-tool-pipeline-run"
version = "0.1.0"
description = "Pipeline runner tool module for Amplifier - invoke DOT graph pipelines at runtime"
license = "MIT"
requires-python = ">=3.11"
authors = [
    { name = "Microsoft MADE:Explorations Team" },
]
dependencies = [
    "amplifier-core",
]

[project.entry-points."amplifier.modules"]
tool-pipeline-run = "amplifier_module_tool_pipeline_run:mount"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
package = true

[tool.hatch.build.targets.wheel]
packages = ["amplifier_module_tool_pipeline_run"]

[tool.hatch.metadata]
allow-direct-references = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--import-mode=importlib"
asyncio_mode = "strict"

[dependency-groups]
dev = [
    "pytest>=9.0.2",
    "pytest-asyncio>=1.3.0",
]
```

Key points for the implementer:
- The entry-point name is `tool-pipeline-run` and it points to the `mount` function in `amplifier_module_tool_pipeline_run`.
- `dependencies` lists only `amplifier-core`. The `loop-pipeline` module is a sibling in the same repo, imported at runtime (not a pip dependency).
- This mirrors `modules/tool-report-outcome/pyproject.toml` exactly in structure.

### Step 2: Create empty `__init__.py` for the package

Create `modules/tool-pipeline-run/amplifier_module_tool_pipeline_run/__init__.py` with just a module docstring and the module type marker:

```python
"""Pipeline runner tool module for Amplifier.

Exposes a `run_pipeline` tool that lets an interactive agent invoke
DOT graph pipelines at runtime via session.spawn.
"""

# Amplifier module metadata
__amplifier_module_type__ = "tool"
```

This will be fleshed out in Task 2. For now it just needs the metadata marker so Amplifier recognizes it as a tool module.

### Step 3: Create empty test files

Create `modules/tool-pipeline-run/tests/__init__.py` (empty file).

Create `modules/tool-pipeline-run/tests/test_pipeline_run.py` with:

```python
"""Tests for tool-pipeline-run."""
```

### Step 4: Verify scaffolding builds

Run:
```bash
cd modules/tool-pipeline-run && uv sync && uv run python -c "import amplifier_module_tool_pipeline_run; print('OK')"
```
Expected: prints `OK` with no errors.

### Step 5: Commit

```bash
git add modules/tool-pipeline-run/
git commit -m "feat(tool-pipeline-run): create module scaffolding"
```

---

## Task 2: PipelineRunTool Class Skeleton + Input Validation

**Files:**
- Modify: `modules/tool-pipeline-run/amplifier_module_tool_pipeline_run/__init__.py`
- Modify: `modules/tool-pipeline-run/tests/test_pipeline_run.py`

**Depends on:** Task 1
**Effort:** ~5 minutes

### Step 1: Write tests for tool metadata and input validation

Add to `modules/tool-pipeline-run/tests/test_pipeline_run.py`:

```python
"""Tests for tool-pipeline-run."""

import pytest

from amplifier_module_tool_pipeline_run import PipelineRunTool


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
    assert "dot_file" in result.error["message"] or "dot_source" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_goal_rejected():
    """Empty string goal returns error."""
    tool = PipelineRunTool(config={})
    result = await tool.execute({"goal": "", "dot_source": "digraph { start -> done }"})
    assert not result.success
    assert "goal" in result.error["message"].lower()
```

### Step 2: Run tests to verify they fail

Run:
```bash
cd modules/tool-pipeline-run && uv run pytest tests/test_pipeline_run.py -v
```
Expected: FAIL — `PipelineRunTool` not yet importable from `__init__.py`.

### Step 3: Implement PipelineRunTool skeleton

Replace `modules/tool-pipeline-run/amplifier_module_tool_pipeline_run/__init__.py` with:

```python
"""Pipeline runner tool module for Amplifier.

Exposes a `run_pipeline` tool that lets an interactive agent invoke
DOT graph pipelines at runtime via session.spawn.
"""

# Amplifier module metadata
__amplifier_module_type__ = "tool"

import logging
from typing import Any

__all__ = ["PipelineRunTool", "mount"]

logger = logging.getLogger(__name__)


class PipelineRunTool:
    """Invoke a DOT graph pipeline at runtime.

    The LLM calls this tool with a DOT pipeline definition (file path
    or inline source) and a goal. The tool spawns a child session
    running the pipeline orchestrator, waits for completion, and
    returns the result.
    """

    name = "run_pipeline"
    description = (
        "Run a DOT graph pipeline. Provide a pipeline definition via "
        "'dot_file' (path to a .dot file, supports @attractor:... mentions) "
        "or 'dot_source' (inline DOT digraph string), plus a 'goal' "
        "describing the task. The pipeline executes as a child session "
        "and returns the result when complete."
    )

    def __init__(self, config: dict[str, Any], coordinator: Any = None) -> None:
        self.config = config
        self.coordinator = coordinator

    @property
    def input_schema(self) -> dict:
        """Return JSON schema for tool parameters."""
        return {
            "type": "object",
            "properties": {
                "dot_file": {
                    "type": "string",
                    "description": (
                        "Path to a .dot pipeline file. Supports @mention "
                        "syntax (e.g. @attractor:examples/pipelines/01-simple-linear.dot)."
                    ),
                },
                "dot_source": {
                    "type": "string",
                    "description": "Inline DOT digraph string.",
                },
                "goal": {
                    "type": "string",
                    "description": (
                        "The goal or task description for the pipeline. "
                        "This replaces $goal in node prompts."
                    ),
                },
                "provider": {
                    "type": "string",
                    "description": (
                        "Override the default provider for all nodes "
                        "(e.g. 'anthropic', 'openai', 'gemini'). Optional."
                    ),
                },
            },
            "required": ["goal"],
        }

    async def execute(self, input: dict[str, Any]) -> Any:
        """Execute the run_pipeline tool."""
        from amplifier_core import ToolResult

        # --- Input validation ---
        goal = input.get("goal", "").strip()
        if not goal:
            return ToolResult(
                success=False,
                error={"message": "goal is required and must be non-empty"},
            )

        dot_file = input.get("dot_file")
        dot_source = input.get("dot_source")
        if not dot_file and not dot_source:
            return ToolResult(
                success=False,
                error={
                    "message": (
                        "Either dot_file or dot_source is required. "
                        "Provide a path to a .dot file or an inline DOT digraph string."
                    )
                },
            )

        # Remaining execution implemented in Tasks 3-6
        return ToolResult(
            success=False,
            error={"message": "Not yet implemented"},
        )


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the run_pipeline tool."""
    config = config or {}
    tool = PipelineRunTool(config, coordinator)
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("Mounted run_pipeline tool")
```

Key points for the implementer:
- The `mount()` function signature matches the Amplifier module contract: `async def mount(coordinator, config)`.
- The tool is registered via `coordinator.mount("tools", tool, name=tool.name)` — this matches the pattern used by `tool-report-outcome`.
- The `input_schema` property returns a JSON Schema dict (not a Pydantic model). This is the contract that `amplifier-core` uses to generate the tool definition sent to the LLM.
- `execute(input)` receives a `dict[str, Any]` (the LLM's tool call arguments) and returns a `ToolResult`.

### Step 4: Run tests to verify they pass

Run:
```bash
cd modules/tool-pipeline-run && uv run pytest tests/test_pipeline_run.py -v
```
Expected: All 6 tests PASS.

### Step 5: Commit

```bash
git add modules/tool-pipeline-run/
git commit -m "feat(tool-pipeline-run): add PipelineRunTool skeleton with input validation"
```

---

## Task 3: DOT Resolution (File Path, Inline, @mention)

**Files:**
- Modify: `modules/tool-pipeline-run/amplifier_module_tool_pipeline_run/__init__.py`
- Modify: `modules/tool-pipeline-run/tests/test_pipeline_run.py`

**Depends on:** Task 2
**Effort:** ~5 minutes

### Step 1: Write tests for DOT resolution

Add these tests to `tests/test_pipeline_run.py`:

```python
import os
import tempfile
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# DOT source resolution
# ---------------------------------------------------------------------------

MINIMAL_DOT = 'digraph Test { start [shape=Mdiamond]; done [shape=Msquare]; start -> done }'


@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_inline_dot_source():
    """Inline dot_source is used directly."""
    tool = PipelineRunTool(config={})
    resolved = tool._resolve_dot_source(dot_file=None, dot_source=MINIMAL_DOT)
    assert resolved == MINIMAL_DOT


@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_dot_file_path():
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


@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_dot_file_not_found():
    """Non-existent dot_file raises FileNotFoundError."""
    tool = PipelineRunTool(config={})
    with pytest.raises(FileNotFoundError):
        tool._resolve_dot_source(dot_file="/nonexistent/path.dot", dot_source=None)


@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_at_mention_path():
    """@mention path is resolved via coordinator mention_resolver capability."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".dot", delete=False) as f:
        f.write(MINIMAL_DOT)
        f.flush()
        tmp_path = f.name
    try:
        # Mock mention resolver
        mock_resolver = MagicMock()
        from pathlib import Path
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


@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_at_mention_no_resolver():
    """@mention path with no mention_resolver returns error."""
    mock_coordinator = MagicMock()
    mock_coordinator.get_capability.return_value = None

    tool = PipelineRunTool(config={}, coordinator=mock_coordinator)
    with pytest.raises(ValueError, match="mention_resolver"):
        tool._resolve_dot_source(
            dot_file="@attractor:some/path.dot",
            dot_source=None,
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_dot_source_takes_precedence_over_dot_file():
    """When both dot_source and dot_file are provided, dot_source wins."""
    tool = PipelineRunTool(config={})
    resolved = tool._resolve_dot_source(
        dot_file="/some/file.dot",
        dot_source=MINIMAL_DOT,
    )
    assert resolved == MINIMAL_DOT
```

### Step 2: Run tests to verify they fail

Run:
```bash
cd modules/tool-pipeline-run && uv run pytest tests/test_pipeline_run.py -v -k "resolve"
```
Expected: FAIL — `_resolve_dot_source` method does not exist yet.

### Step 3: Implement `_resolve_dot_source` method

Add this method to the `PipelineRunTool` class in `__init__.py`, after `__init__`:

```python
    def _resolve_dot_source(
        self,
        dot_file: str | None,
        dot_source: str | None,
    ) -> str:
        """Resolve DOT source from file path or inline string.

        Resolution priority:
        1. dot_source (inline string) — used as-is if provided
        2. dot_file (file path) — read from disk; supports @mention syntax

        Args:
            dot_file: Path to a .dot file (supports @mention syntax).
            dot_source: Inline DOT digraph string.

        Returns:
            The DOT source string.

        Raises:
            FileNotFoundError: If dot_file path does not exist.
            ValueError: If @mention path cannot be resolved.
        """
        from pathlib import Path

        # Priority 1: inline source
        if dot_source:
            return dot_source

        # Priority 2: file path
        if not dot_file:
            raise ValueError("Either dot_file or dot_source must be provided")

        # Handle @mention syntax
        if dot_file.startswith("@"):
            if self.coordinator is None:
                raise ValueError(
                    "Cannot resolve @mention path without a coordinator. "
                    "The mention_resolver capability is required."
                )
            mention_resolver = self.coordinator.get_capability("mention_resolver")
            if mention_resolver is None:
                raise ValueError(
                    "Cannot resolve @mention path: mention_resolver capability "
                    "not available. Ensure the bundle is properly configured."
                )
            resolved_path = mention_resolver.resolve(dot_file)
            if resolved_path is None:
                raise FileNotFoundError(
                    f"Could not resolve @mention path: {dot_file}"
                )
            file_path = Path(resolved_path)
        else:
            file_path = Path(dot_file)

        if not file_path.exists():
            raise FileNotFoundError(f"DOT file not found: {file_path}")

        return file_path.read_text()
```

Also update the `execute()` method to call `_resolve_dot_source` (add after the validation block, replacing the "Not yet implemented" placeholder):

```python
        # --- Resolve DOT source ---
        try:
            dot_source_resolved = self._resolve_dot_source(
                dot_file=dot_file,
                dot_source=dot_source,
            )
        except FileNotFoundError as e:
            return ToolResult(
                success=False,
                error={"message": f"DOT file not found: {e}"},
            )
        except ValueError as e:
            return ToolResult(
                success=False,
                error={"message": str(e)},
            )

        # Remaining execution implemented in Tasks 4-6
        return ToolResult(
            success=False,
            error={"message": "Not yet implemented: spawn execution"},
        )
```

### Step 4: Run tests to verify they pass

Run:
```bash
cd modules/tool-pipeline-run && uv run pytest tests/test_pipeline_run.py -v
```
Expected: All tests PASS.

### Step 5: Commit

```bash
git add modules/tool-pipeline-run/
git commit -m "feat(tool-pipeline-run): implement DOT source resolution with @mention support"
```

---

## Task 4: Provider Validation

**Files:**
- Modify: `modules/tool-pipeline-run/amplifier_module_tool_pipeline_run/__init__.py`
- Modify: `modules/tool-pipeline-run/tests/test_pipeline_run.py`

**Depends on:** Task 3
**Effort:** ~5 minutes

### Step 1: Write tests for provider validation

Add to `tests/test_pipeline_run.py`:

```python
# ---------------------------------------------------------------------------
# Provider validation
# ---------------------------------------------------------------------------

# DOT source with model_stylesheet that requires anthropic and openai
DOT_WITH_STYLESHEET = '''digraph Test {
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
}'''

# DOT source with explicit llm_provider on a node (no stylesheet)
DOT_WITH_NODE_PROVIDER = '''digraph Test {
    start [shape=Mdiamond]
    impl [llm_provider="gemini", prompt="Implement"]
    done [shape=Msquare]
    start -> impl -> done
}'''

# DOT source with no providers specified at all
DOT_NO_PROVIDERS = '''digraph Test {
    start [shape=Mdiamond]
    impl [prompt="Implement"]
    done [shape=Msquare]
    start -> impl -> done
}'''


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
```

### Step 2: Run tests to verify they fail

Run:
```bash
cd modules/tool-pipeline-run && uv run pytest tests/test_pipeline_run.py -v -k "provider"
```
Expected: FAIL — methods do not exist yet.

### Step 3: Implement provider validation methods

Add these methods to `PipelineRunTool` in `__init__.py`:

```python
    def _extract_required_providers(self, dot_source: str) -> set[str]:
        """Parse a DOT source and extract all required LLM providers.

        Checks two sources:
        1. model_stylesheet rules — each rule with an llm_provider declaration
        2. Node-level llm_provider attributes — explicit per-node settings

        Structural nodes (Mdiamond/start, Msquare/exit) are excluded since
        they don't invoke an LLM.

        Args:
            dot_source: The DOT digraph source string.

        Returns:
            Set of provider names (e.g. {"anthropic", "openai"}).
        """
        from amplifier_module_loop_pipeline.dot_parser import parse_dot
        from amplifier_module_loop_pipeline.stylesheet import parse_stylesheet

        graph = parse_dot(dot_source)
        providers: set[str] = set()

        # Source 1: model_stylesheet rules
        if graph.model_stylesheet:
            rules = parse_stylesheet(graph.model_stylesheet)
            for rule in rules:
                provider = rule.properties.get("llm_provider")
                if provider:
                    providers.add(provider)

        # Source 2: explicit node attributes
        structural_shapes = {"Mdiamond", "Msquare", "point"}
        for node in graph.nodes.values():
            if node.shape in structural_shapes:
                continue
            provider = node.attrs.get("llm_provider")
            if provider:
                providers.add(provider)

        return providers

    def _check_missing_providers(
        self,
        required: set[str],
        available: set[str],
    ) -> set[str]:
        """Check which required providers are missing from available set.

        Args:
            required: Provider names required by the pipeline.
            available: Provider names available in the agent configuration.

        Returns:
            Set of missing provider names (empty if all present).
        """
        return required - available

    def _get_available_providers(self) -> set[str]:
        """Get available provider names from coordinator config.

        Reads the agent configs from the coordinator to determine which
        provider profiles are registered. Falls back to the profiles
        mapping in config if available.

        Returns:
            Set of available provider name strings.
        """
        available: set[str] = set()

        if self.coordinator is None:
            return available

        # Check config for explicit profiles mapping
        profiles = self.config.get("profiles", {})
        if isinstance(profiles, dict):
            available.update(profiles.keys())

        # Also check coordinator's agent configs for auto-discovery
        coordinator_config = getattr(self.coordinator, "config", None) or {}
        agents = coordinator_config.get("agents", {})
        for agent_name in agents:
            # Agent names like "attractor-agent-anthropic" -> extract "anthropic"
            # Also accept direct provider names as agent keys
            available.add(agent_name)

        return available
```

Important notes for the implementer:
- `_extract_required_providers()` imports `parse_dot` from the sibling `loop-pipeline` module. This works because both modules are installed in the same environment. The import is inside the method (not at module top level) to avoid a hard import dependency at load time.
- Structural shapes (`Mdiamond`, `Msquare`, `point`) are excluded because those are start/exit/join nodes that don't run LLM calls.
- `_get_available_providers()` mirrors the profile resolution logic from `_build_backend()` in `loop-pipeline/__init__.py` (lines 196-265).

### Step 4: Wire validation into `execute()`

Update the `execute()` method. After DOT resolution and before the "not yet implemented" placeholder, add:

```python
        # --- Parse and validate providers ---
        try:
            required_providers = self._extract_required_providers(dot_source_resolved)
        except Exception as e:
            return ToolResult(
                success=False,
                error={"message": f"Failed to parse DOT source: {e}"},
            )

        if required_providers:
            available_providers = self._get_available_providers()
            missing = self._check_missing_providers(required_providers, available_providers)
            if missing:
                return ToolResult(
                    success=False,
                    error={
                        "message": (
                            f"Pipeline requires providers not available in this session: "
                            f"{', '.join(sorted(missing))}. "
                            f"Available: {', '.join(sorted(available_providers)) or 'none'}. "
                            f"Configure the missing providers in the pipeline-runner agent."
                        ),
                        "missing_providers": sorted(missing),
                        "available_providers": sorted(available_providers),
                    },
                )
```

### Step 5: Run tests to verify they pass

Run:
```bash
cd modules/tool-pipeline-run && uv run pytest tests/test_pipeline_run.py -v
```
Expected: All tests PASS.

### Step 6: Commit

```bash
git add modules/tool-pipeline-run/
git commit -m "feat(tool-pipeline-run): add provider validation from DOT stylesheet and node attrs"
```

---

## Task 5: Spawn Execution + ToolResult

**Files:**
- Modify: `modules/tool-pipeline-run/amplifier_module_tool_pipeline_run/__init__.py`
- Modify: `modules/tool-pipeline-run/tests/test_pipeline_run.py`

**Depends on:** Task 4
**Effort:** ~5 minutes

### Step 1: Write tests for spawn execution

Add to `tests/test_pipeline_run.py`:

```python
from unittest.mock import AsyncMock


# ---------------------------------------------------------------------------
# Spawn execution
# ---------------------------------------------------------------------------

SIMPLE_DOT = '''digraph Test {
    start [shape=Mdiamond]
    impl [prompt="Do the thing"]
    done [shape=Msquare]
    start -> impl -> done
}'''


@pytest.mark.asyncio(loop_scope="session")
async def test_no_spawn_capability_returns_error():
    """When session.spawn is not available, returns a clear error."""
    mock_coordinator = MagicMock()
    mock_coordinator.get_capability.return_value = None
    mock_coordinator.config = {}

    tool = PipelineRunTool(config={}, coordinator=mock_coordinator)
    result = await tool.execute({
        "goal": "test goal",
        "dot_source": SIMPLE_DOT,
    })
    assert not result.success
    assert "session.spawn" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_successful_spawn_returns_result():
    """Successful pipeline spawn returns structured result."""
    mock_spawn = AsyncMock(return_value={
        "output": '{"status": "success", "notes": "Pipeline completed"}',
        "session_id": "child-session-123",
    })

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
    result = await tool.execute({
        "goal": "test goal",
        "dot_source": SIMPLE_DOT,
    })

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
    await tool.execute({
        "goal": "build a widget",
        "dot_source": SIMPLE_DOT,
    })

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
    result = await tool.execute({
        "goal": "test",
        "dot_source": SIMPLE_DOT,
    })
    assert not result.success
    assert "spawn failed" in result.error["message"]
```

### Step 2: Run tests to verify they fail

Run:
```bash
cd modules/tool-pipeline-run && uv run pytest tests/test_pipeline_run.py -v -k "spawn"
```
Expected: FAIL — spawn logic not yet implemented.

### Step 3: Implement spawn execution

Replace the "Not yet implemented: spawn execution" placeholder in `execute()` with the full spawn implementation:

```python
        # --- Get session.spawn capability ---
        spawn_fn = None
        if self.coordinator is not None and hasattr(self.coordinator, "get_capability"):
            spawn_fn = self.coordinator.get_capability("session.spawn")

        if spawn_fn is None:
            return ToolResult(
                success=False,
                error={
                    "message": (
                        "session.spawn capability is not available. "
                        "Pipeline execution requires the ability to spawn "
                        "child sessions. Ensure you are running in an "
                        "environment that supports session spawning (e.g. the CLI)."
                    )
                },
            )

        # --- Resolve runner agent name ---
        runner_agent = self.config.get("runner_agent", "attractor-pipeline-runner")

        # --- Build orchestrator config for the child session ---
        orchestrator_config: dict[str, Any] = {
            "dot_source": dot_source_resolved,
        }

        # Forward profiles from our config if present
        profiles = self.config.get("profiles")
        if profiles:
            orchestrator_config["profiles"] = profiles

        # --- Build spawn kwargs ---
        parent_session = getattr(self.coordinator, "session", None)
        coordinator_config = getattr(self.coordinator, "config", None) or {}
        agent_configs = coordinator_config.get("agents", {})

        spawn_kwargs: dict[str, Any] = {
            "agent_name": runner_agent,
            "instruction": goal,
            "parent_session": parent_session,
            "agent_configs": agent_configs,
            "orchestrator_config": orchestrator_config,
        }

        # Optional provider override
        provider_override = input.get("provider")
        if provider_override:
            try:
                from amplifier_foundation import ProviderPreference
                spawn_kwargs["provider_preferences"] = [
                    ProviderPreference(provider=provider_override, model="*")
                ]
            except ImportError:
                logger.debug("amplifier_foundation not available for provider override")

        # --- Execute spawn ---
        import time
        start_time = time.monotonic()

        try:
            result = await spawn_fn(**spawn_kwargs)
        except Exception as e:
            logger.warning("Pipeline spawn failed: %s", e)
            return ToolResult(
                success=False,
                error={
                    "message": f"Pipeline execution failed: {e}",
                    "type": type(e).__name__,
                },
            )

        duration = round(time.monotonic() - start_time, 1)

        # --- Parse result ---
        output = result.get("output", "") if isinstance(result, dict) else str(result)
        session_id = result.get("session_id", "unknown") if isinstance(result, dict) else "unknown"

        # Try to parse structured outcome from pipeline output
        import json
        pipeline_status = "success"
        pipeline_notes = ""
        try:
            parsed = json.loads(output) if output.strip().startswith("{") else {}
            pipeline_status = parsed.get("status", "success")
            pipeline_notes = parsed.get("notes", "")
        except (json.JSONDecodeError, AttributeError):
            pipeline_notes = output[:500] if output else "Pipeline completed"

        return ToolResult(
            success=True,
            output={
                "status": pipeline_status,
                "session_id": session_id,
                "notes": pipeline_notes,
                "duration_seconds": duration,
                "runner_agent": runner_agent,
            },
        )
```

Key points for the implementer:
- The `runner_agent` name defaults to `"attractor-pipeline-runner"` but is configurable via `config["runner_agent"]`. This is the agent definition created in Task 7.
- `orchestrator_config["dot_source"]` passes the DOT inline to the child session's `loop-pipeline` orchestrator. Look at `PipelineOrchestrator._resolve_dot_source()` in `loop-pipeline/__init__.py:386-398` — it reads `config["dot_source"]` first, then `config["dot_file"]`.
- The `spawn_kwargs` structure matches what `AmplifierBackend._run_with_spawn()` sends in `backend.py:208-221`. The same `session.spawn` function is being called.
- The result parsing mirrors `_parse_outcome()` in `backend.py:354-383`.

### Step 4: Run tests to verify they pass

Run:
```bash
cd modules/tool-pipeline-run && uv run pytest tests/test_pipeline_run.py -v
```
Expected: All tests PASS.

### Step 5: Commit

```bash
git add modules/tool-pipeline-run/
git commit -m "feat(tool-pipeline-run): implement spawn execution with structured result parsing"
```

---

## Task 6: Progress Reporting via DisplaySystem + Hook Events

**Files:**
- Modify: `modules/tool-pipeline-run/amplifier_module_tool_pipeline_run/__init__.py`
- Modify: `modules/tool-pipeline-run/tests/test_pipeline_run.py`

**Depends on:** Task 5
**Effort:** ~4 minutes

### Step 1: Write tests for progress reporting

Add to `tests/test_pipeline_run.py`:

```python
# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_display_system_start_message():
    """DisplaySystem receives a start message when pipeline begins."""
    mock_spawn = AsyncMock(return_value={
        "output": '{"status": "success"}',
        "session_id": "child-123",
    })

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
    await tool.execute({
        "goal": "test goal",
        "dot_source": SIMPLE_DOT,
    })

    # DisplaySystem should have been called at least for start
    assert mock_display.show_message.call_count >= 1
    first_call_msg = mock_display.show_message.call_args_list[0][0][0]
    assert "pipeline" in first_call_msg.lower()


@pytest.mark.asyncio(loop_scope="session")
async def test_hook_events_emitted():
    """Hook events are emitted for pipeline start and complete."""
    mock_spawn = AsyncMock(return_value={
        "output": '{"status": "success"}',
        "session_id": "child-123",
    })

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
    await tool.execute({
        "goal": "test goal",
        "dot_source": SIMPLE_DOT,
    })

    # Should have emitted start and complete events
    event_names = [call[0][0] for call in mock_hooks.emit.call_args_list]
    assert "pipeline:tool:start" in event_names
    assert "pipeline:tool:complete" in event_names


@pytest.mark.asyncio(loop_scope="session")
async def test_no_crash_without_display_or_hooks():
    """Progress reporting is graceful when display_system and hooks are absent."""
    mock_spawn = AsyncMock(return_value={
        "output": '{"status": "success"}',
        "session_id": "child-123",
    })

    mock_coordinator = MagicMock(spec=[])  # empty spec = no attributes
    mock_coordinator.get_capability = lambda name: mock_spawn if name == "session.spawn" else None
    mock_coordinator.config = {"agents": {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=mock_coordinator,
    )
    # Should not crash
    result = await tool.execute({
        "goal": "test",
        "dot_source": SIMPLE_DOT,
    })
    assert result.success
```

### Step 2: Run tests to verify they fail

Run:
```bash
cd modules/tool-pipeline-run && uv run pytest tests/test_pipeline_run.py -v -k "display or hook_events or no_crash"
```
Expected: FAIL — progress reporting not yet wired in.

### Step 3: Implement progress reporting helpers

Add these private methods to `PipelineRunTool`:

```python
    def _show_progress(self, message: str) -> None:
        """Show a progress message via DisplaySystem side-channel.

        Silently no-ops if DisplaySystem is not available.
        """
        if self.coordinator is None:
            return
        display_system = getattr(self.coordinator, "display_system", None)
        if display_system is not None and hasattr(display_system, "show_message"):
            try:
                display_system.show_message(message)
            except Exception:
                logger.debug("Failed to show progress message", exc_info=True)

    async def _emit_event(self, event_name: str, data: dict[str, Any]) -> None:
        """Emit a hook event.

        Silently no-ops if hooks are not available.
        """
        if self.coordinator is None:
            return
        hooks = getattr(self.coordinator, "hooks", None)
        if hooks is not None and hasattr(hooks, "emit"):
            try:
                await hooks.emit(event_name, data)
            except Exception:
                logger.debug("Failed to emit event %s", event_name, exc_info=True)
```

### Step 4: Wire progress reporting into execute()

In the `execute()` method, add progress reporting around the spawn call. Insert **before** the spawn call:

```python
        # --- Progress: pipeline starting ---
        self._show_progress(
            f"Starting pipeline (runner: {runner_agent})..."
        )
        await self._emit_event("pipeline:tool:start", {
            "goal": goal,
            "runner_agent": runner_agent,
            "dot_file": dot_file,
        })
```

And **after** the result parsing (right before the final `return ToolResult(...)`):

```python
        # --- Progress: pipeline complete ---
        self._show_progress(
            f"Pipeline complete: {pipeline_status} ({duration}s)"
        )
        await self._emit_event("pipeline:tool:complete", {
            "status": pipeline_status,
            "session_id": session_id,
            "duration_seconds": duration,
            "notes": pipeline_notes,
        })
```

Also, in the spawn exception handler, add an event before returning:

```python
        except Exception as e:
            logger.warning("Pipeline spawn failed: %s", e)
            await self._emit_event("pipeline:tool:complete", {
                "status": "error",
                "error": str(e),
            })
            return ToolResult(
                ...
            )
```

### Step 5: Run tests to verify they pass

Run:
```bash
cd modules/tool-pipeline-run && uv run pytest tests/test_pipeline_run.py -v
```
Expected: All tests PASS.

### Step 6: Commit

```bash
git add modules/tool-pipeline-run/
git commit -m "feat(tool-pipeline-run): add progress reporting via DisplaySystem and hook events"
```

---

## Task 7: Pipeline Runner Agent Definition

**Files:**
- Create: `agents/pipeline-runner.yaml`

**Depends on:** Task 1 (only needs to know module paths)
**Effort:** ~3 minutes

### Step 1: Write the agent definition

Create `agents/pipeline-runner.yaml`:

```yaml
# Pipeline runner agent — spawned by tool-pipeline-run to execute DOT pipelines.
#
# This agent uses loop-pipeline as its orchestrator. The DOT source and goal
# are injected at spawn time via orchestrator_config. It has all three
# provider profiles as child agents so the model stylesheet can route nodes
# to any provider.

bundle:
  name: attractor-pipeline-runner
  version: 0.1.0
  description: >
    Pipeline execution agent. Spawned by the run_pipeline tool to execute
    a DOT graph pipeline. Uses loop-pipeline orchestrator with all three
    provider profiles available for model stylesheet routing.

includes:
  - bundle: attractor:behaviors/attractor-core

# The orchestrator provider (used for pipeline orchestrator reasoning).
# This is the LLM that the pipeline engine itself uses, not the per-node LLMs.
providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
    config:
      default_model: claude-sonnet-4-20250514

# Pipeline orchestrator session.
# dot_source or dot_file is injected at spawn time via orchestrator_config.
session:
  orchestrator:
    module: loop-pipeline
    source: ../modules/loop-pipeline
    config:
      # Profiles map llm_provider values to child agent names.
      profiles:
        anthropic: attractor-agent-anthropic
        openai: attractor-agent-openai
        gemini: attractor-agent-gemini
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main

# Orchestrator-level tools (filesystem access for the pipeline engine itself)
tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
    config:
      timeout: 120
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main

# Child agents (one per provider) — spawned by the pipeline engine per node.
# These are identical to the agents in bundles/attractor-pipeline.yaml.
agents:
  attractor-agent-anthropic:
    providers:
      - module: provider-anthropic
        source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
        config:
          default_model: claude-sonnet-4-20250514
          timeout: 600
    session:
      orchestrator:
        module: loop-agent
        source: ../modules/loop-agent
        config:
          max_tool_rounds_per_input: 50
          default_command_timeout_ms: 120000
      context:
        module: context-simple
        source: git+https://github.com/microsoft/amplifier-module-context-simple@main
    includes:
      - bundle: attractor:behaviors/attractor-core
    tools:
      - module: tool-filesystem
        source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
      - module: tool-bash
        source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
        config:
          timeout: 120
      - module: tool-search
        source: git+https://github.com/microsoft/amplifier-module-tool-search@main
    context:
      include:
        - context/system-anthropic.md

  attractor-agent-openai:
    providers:
      - module: provider-openai
        source: git+https://github.com/microsoft/amplifier-module-provider-openai@main
        config:
          default_model: gpt-4.1
          timeout: 600
    session:
      orchestrator:
        module: loop-agent
        source: ../modules/loop-agent
        config:
          max_tool_rounds_per_input: 50
          default_command_timeout_ms: 120000
      context:
        module: context-simple
        source: git+https://github.com/microsoft/amplifier-module-context-simple@main
    includes:
      - bundle: attractor:behaviors/attractor-core
    tools:
      - module: tool-apply-patch
        source: ../modules/tool-apply-patch
      - module: tool-filesystem
        source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
        config:
          expose: [read_file, write_file]
      - module: tool-bash
        source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
        config:
          timeout: 120
      - module: tool-search
        source: git+https://github.com/microsoft/amplifier-module-tool-search@main
    context:
      include:
        - context/system-openai.md

  attractor-agent-gemini:
    providers:
      - module: provider-gemini
        source: git+https://github.com/microsoft/amplifier-module-provider-gemini@main
        config:
          default_model: gemini-2.5-pro
          timeout: 600
    session:
      orchestrator:
        module: loop-agent
        source: ../modules/loop-agent
        config:
          max_tool_rounds_per_input: 50
          default_command_timeout_ms: 120000
      context:
        module: context-simple
        source: git+https://github.com/microsoft/amplifier-module-context-simple@main
    includes:
      - bundle: attractor:behaviors/attractor-core
    tools:
      - module: tool-filesystem
        source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
      - module: tool-bash
        source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
        config:
          timeout: 120
      - module: tool-search
        source: git+https://github.com/microsoft/amplifier-module-tool-search@main
      - module: tool-web
        source: git+https://github.com/microsoft/amplifier-module-tool-web@main
    context:
      include:
        - context/system-gemini.md
```

Key points for the implementer:
- This is structurally identical to `bundles/attractor-pipeline.yaml` (the existing pipeline entry point). The difference is that this agent is *spawned* by the tool at runtime rather than being the top-level session.
- The `profiles` mapping (`anthropic -> attractor-agent-anthropic`, etc.) must match exactly. This is how `AmplifierBackend` in `backend.py` resolves which child agent to spawn per pipeline node.
- The `dot_source` or `dot_file` config value is **not** set here — it's injected at spawn time via `orchestrator_config` by the tool.

### Step 2: Verify YAML is valid

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('agents/pipeline-runner.yaml')); print('YAML valid')"
```
Expected: `YAML valid`

### Step 3: Commit

```bash
git add agents/pipeline-runner.yaml
git commit -m "feat(tool-pipeline-run): add pipeline-runner agent definition"
```

---

## Task 8: Bundle Wiring (Context + Behavior + Entry Point)

**Files:**
- Create: `context/pipeline-awareness.md`
- Create: `bundles/attractor-interactive.yaml`
- Modify: `bundle.md` (add agents entry for pipeline-runner)

**Depends on:** Task 7
**Effort:** ~5 minutes

### Step 1: Create pipeline-awareness context

Create `context/pipeline-awareness.md`:

```markdown
# Pipeline Capabilities

You have access to the `run_pipeline` tool which can execute DOT graph pipelines.

## When to Use Pipelines

Use `run_pipeline` when the user asks you to:
- Run a pipeline or workflow defined in a `.dot` file
- Execute a multi-step coding pipeline
- Run an Attractor pipeline

## How to Use

Call `run_pipeline` with:
- **`goal`** (required): The task description. This replaces `$goal` in node prompts.
- **`dot_file`** (optional): Path to a `.dot` file. Supports `@attractor:` mentions.
- **`dot_source`** (optional): Inline DOT digraph string.

You must provide either `dot_file` or `dot_source`.

## Examples

Run a pipeline from a file:
```json
{
  "goal": "Refactor the authentication module to use async patterns",
  "dot_file": "@attractor:examples/pipelines/02-plan-implement-test.dot"
}
```

Run a simple inline pipeline:
```json
{
  "goal": "Add input validation to the user registration endpoint",
  "dot_source": "digraph { start [shape=Mdiamond]; implement [prompt=\"$goal\"]; test [prompt=\"Write tests for the changes\"]; done [shape=Msquare]; start -> implement -> test -> done }"
}
```

## Available Example Pipelines

- `@attractor:examples/pipelines/01-simple-linear.dot` — Minimal start -> implement -> done
- `@attractor:examples/pipelines/02-plan-implement-test.dot` — Plan, implement, test cycle
- `@attractor:examples/pipelines/03-conditional-routing.dot` — Conditional branching based on outcomes
- `@attractor:examples/pipelines/04-retry-with-fallback.dot` — Retry logic with fallback paths
- `@attractor:examples/pipelines/06-model-stylesheet.dot` — Multi-provider model selection
```

### Step 2: Create the interactive bundle entry point

Create `bundles/attractor-interactive.yaml`:

```yaml
# Interactive Attractor session with pipeline execution capability.
#
# This bundle creates an interactive coding agent that can also invoke
# DOT graph pipelines at runtime via the run_pipeline tool. Combines:
# - An interactive agent loop (loop-agent) for conversation
# - The run_pipeline tool for pipeline invocation
# - Pipeline-awareness context so the agent knows about pipelines
# - The pipeline-runner agent registered for spawning

bundle:
  name: attractor-interactive
  version: 0.1.0
  description: >
    Interactive Attractor session with pipeline execution capabilities.
    An agent that can converse normally AND invoke DOT graph pipelines
    on demand via the run_pipeline tool.

includes:
  - bundle: attractor:behaviors/attractor-core
  - bundle: git+https://github.com/microsoft/amplifier-foundation@main

# Interactive session provider
providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
    config:
      default_model: claude-sonnet-4-20250514

# Interactive agent loop (not pipeline — the user talks to this)
session:
  orchestrator:
    module: loop-agent
    source: ../modules/loop-agent
    config:
      max_tool_rounds_per_input: 50
      default_command_timeout_ms: 120000
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main

# Tools: standard coding tools + pipeline runner
tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
    config:
      timeout: 120
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main
  - module: tool-pipeline-run
    source: ../modules/tool-pipeline-run
    config:
      runner_agent: attractor-pipeline-runner
      profiles:
        anthropic: attractor-agent-anthropic
        openai: attractor-agent-openai
        gemini: attractor-agent-gemini

# Context: standard system prompt + pipeline awareness
context:
  include:
    - context/system-anthropic.md
    - context/pipeline-awareness.md

# The pipeline runner agent (spawned by tool-pipeline-run)
agents:
  attractor-pipeline-runner:
    description: Pipeline execution agent spawned by run_pipeline tool
    session:
      orchestrator:
        module: loop-pipeline
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-pipeline
        config:
          profiles:
            anthropic: attractor-agent-anthropic
            openai: attractor-agent-openai
            gemini: attractor-agent-gemini
```

Key points for the implementer:
- The session orchestrator is `loop-agent` (interactive), NOT `loop-pipeline`. This agent is conversational — it uses `run_pipeline` as a tool when the user asks for pipeline execution.
- `tool-pipeline-run` is mounted with `config.runner_agent = "attractor-pipeline-runner"` and `config.profiles` mapping. The tool reads these in `execute()`.
- The `agents` section registers `attractor-pipeline-runner` so `session.spawn` can find it when the tool calls `spawn_fn(agent_name="attractor-pipeline-runner", ...)`.

### Step 3: Update bundle.md to register the pipeline-runner agent

In `bundle.md`, add the pipeline-runner agent to the `agents:` section in the YAML front-matter. Add after the existing `attractor-profile-gemini` entry:

```yaml
  attractor-pipeline-runner:
    description: Pipeline execution agent spawned by run_pipeline tool
    session:
      orchestrator:
        module: loop-pipeline
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-pipeline
        config: {profiles: {anthropic: attractor-agent-anthropic, openai: attractor-agent-openai, gemini: attractor-agent-gemini}}
```

Also add the interactive bundle to the description's entry points list:

```
      bundles/attractor-interactive \u2014 Interactive agent with pipeline tool
```

And add to the Architecture section:

```
\u251c\u2500\u2500 agents/                     # Spawnable agent definitions
\u2502   \u2514\u2500\u2500 pipeline-runner.yaml    # Pipeline execution agent (spawned by tool)
```

### Step 4: Verify YAML validity

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('bundles/attractor-interactive.yaml')); print('interactive OK')"
python3 -c "import yaml; yaml.safe_load(open('agents/pipeline-runner.yaml')); print('runner OK')"
```
Expected: Both print OK.

### Step 5: Commit

```bash
git add context/pipeline-awareness.md bundles/attractor-interactive.yaml bundle.md
git commit -m "feat(tool-pipeline-run): add interactive bundle with pipeline-awareness context"
```

---

## Task 9: Integration Test Script

**Files:**
- Create: `modules/tool-pipeline-run/tests/test_integration.py`

**Depends on:** Tasks 1-8
**Effort:** ~4 minutes

### Step 1: Write integration tests

Create `modules/tool-pipeline-run/tests/test_integration.py`:

```python
"""Integration tests for tool-pipeline-run.

These tests verify the full flow from tool invocation through DOT parsing,
provider validation, and spawn. The spawn itself is mocked (actual pipeline
execution requires a running Amplifier environment).
"""

import json
import os
import tempfile

import pytest
from unittest.mock import AsyncMock, MagicMock

from amplifier_module_tool_pipeline_run import PipelineRunTool


# A realistic pipeline DOT source with stylesheet
REALISTIC_PIPELINE = '''digraph CodeReview {
    graph [
        goal="Review and improve the authentication module",
        label="Code Review Pipeline",
        model_stylesheet="
            * {
                llm_provider: anthropic;
                llm_model: claude-sonnet-4-20250514;
            }
            .planning {
                llm_provider: openai;
                llm_model: o3;
                reasoning_effort: high;
            }
        "
    ]
    rankdir=LR

    start [shape=Mdiamond, label="Start"]
    done  [shape=Msquare, label="Done"]

    plan [
        label="Plan Review",
        class="planning",
        prompt="Analyze $goal and create a review plan."
    ]
    review [
        label="Execute Review",
        prompt="Execute the review plan for: $goal"
    ]
    report [
        label="Write Report",
        prompt="Write a summary report of the review findings."
    ]

    start -> plan -> review -> report -> done
}'''


def _make_coordinator(
    spawn_result: dict | None = None,
    spawn_error: Exception | None = None,
    agents: dict | None = None,
):
    """Create a mock coordinator with configurable spawn behavior."""
    if spawn_error:
        mock_spawn = AsyncMock(side_effect=spawn_error)
    elif spawn_result:
        mock_spawn = AsyncMock(return_value=spawn_result)
    else:
        mock_spawn = AsyncMock(return_value={
            "output": '{"status": "success", "notes": "Done"}',
            "session_id": "test-session-001",
        })

    mock_coordinator = MagicMock()

    def get_cap(name):
        if name == "session.spawn":
            return mock_spawn
        return None

    mock_coordinator.get_capability = get_cap
    mock_coordinator.config = {"agents": agents or {"attractor-pipeline-runner": {}}}
    mock_coordinator.session = MagicMock()
    mock_coordinator.display_system = MagicMock()
    mock_coordinator.display_system.show_message = MagicMock()
    mock_coordinator.hooks = MagicMock()
    mock_coordinator.hooks.emit = AsyncMock()

    return mock_coordinator, mock_spawn


# ---------------------------------------------------------------------------
# End-to-end: inline DOT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_inline_dot():
    """Full flow: inline DOT -> validate -> spawn -> result."""
    coordinator, mock_spawn = _make_coordinator()
    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=coordinator,
    )

    result = await tool.execute({
        "goal": "Review the auth module",
        "dot_source": REALISTIC_PIPELINE,
    })

    assert result.success
    assert result.output["status"] == "success"
    assert result.output["session_id"] == "test-session-001"
    assert result.output["duration_seconds"] >= 0

    # Verify spawn was called with correct args
    mock_spawn.assert_called_once()
    call_kwargs = mock_spawn.call_args[1]
    assert call_kwargs["agent_name"] == "attractor-pipeline-runner"
    assert call_kwargs["instruction"] == "Review the auth module"
    assert "dot_source" in call_kwargs["orchestrator_config"]


# ---------------------------------------------------------------------------
# End-to-end: file-based DOT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_file_dot():
    """Full flow: DOT file path -> read -> validate -> spawn -> result."""
    coordinator, mock_spawn = _make_coordinator()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".dot", delete=False
    ) as f:
        f.write(REALISTIC_PIPELINE)
        f.flush()
        dot_path = f.name

    try:
        tool = PipelineRunTool(
            config={"runner_agent": "attractor-pipeline-runner"},
            coordinator=coordinator,
        )

        result = await tool.execute({
            "goal": "Review the auth module",
            "dot_file": dot_path,
        })

        assert result.success
        assert result.output["status"] == "success"
    finally:
        os.unlink(dot_path)


# ---------------------------------------------------------------------------
# Provider validation integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_missing_provider_rejected():
    """Pipeline requiring unavailable provider is rejected before spawn."""
    coordinator, mock_spawn = _make_coordinator()

    tool = PipelineRunTool(
        config={
            "runner_agent": "attractor-pipeline-runner",
            "profiles": {"anthropic": "attractor-agent-anthropic"},
            # Note: "openai" profile is missing but pipeline requires it
        },
        coordinator=coordinator,
    )

    result = await tool.execute({
        "goal": "Review the auth module",
        "dot_source": REALISTIC_PIPELINE,  # requires anthropic + openai
    })

    assert not result.success
    assert "openai" in result.error["message"]
    # Spawn should NOT have been called
    mock_spawn.assert_not_called()


# ---------------------------------------------------------------------------
# Spawn failure handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_spawn_exception_handled():
    """Spawn exception is caught and returned as tool error."""
    coordinator, _ = _make_coordinator(
        spawn_error=RuntimeError("Connection refused")
    )

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=coordinator,
    )

    result = await tool.execute({
        "goal": "test",
        "dot_source": REALISTIC_PIPELINE,
    })

    assert not result.success
    assert "Connection refused" in result.error["message"]


# ---------------------------------------------------------------------------
# Progress reporting integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_progress_events():
    """Full flow emits start and complete events."""
    coordinator, _ = _make_coordinator()

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=coordinator,
    )

    await tool.execute({
        "goal": "test",
        "dot_source": REALISTIC_PIPELINE,
    })

    # Check hook events
    event_names = [
        call[0][0] for call in coordinator.hooks.emit.call_args_list
    ]
    assert "pipeline:tool:start" in event_names
    assert "pipeline:tool:complete" in event_names

    # Check display messages
    assert coordinator.display_system.show_message.call_count >= 2

    # Start event should contain goal
    start_event_data = None
    for call in coordinator.hooks.emit.call_args_list:
        if call[0][0] == "pipeline:tool:start":
            start_event_data = call[0][1]
            break
    assert start_event_data is not None
    assert start_event_data["goal"] == "test"


# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_pipeline_failure_status():
    """Pipeline that returns failure status is reported correctly."""
    coordinator, _ = _make_coordinator(spawn_result={
        "output": json.dumps({
            "status": "fail",
            "failure_reason": "Tests failed",
            "notes": "3 test failures detected",
        }),
        "session_id": "fail-session-001",
    })

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=coordinator,
    )

    result = await tool.execute({
        "goal": "fix the tests",
        "dot_source": REALISTIC_PIPELINE,
    })

    # Tool call itself succeeds (the pipeline ran), but status is fail
    assert result.success
    assert result.output["status"] == "fail"


@pytest.mark.asyncio(loop_scope="session")
async def test_e2e_plain_text_output():
    """Pipeline that returns plain text (not JSON) is handled gracefully."""
    coordinator, _ = _make_coordinator(spawn_result={
        "output": "All tasks completed successfully.",
        "session_id": "text-session-001",
    })

    tool = PipelineRunTool(
        config={"runner_agent": "attractor-pipeline-runner"},
        coordinator=coordinator,
    )

    result = await tool.execute({
        "goal": "do the thing",
        "dot_source": REALISTIC_PIPELINE,
    })

    assert result.success
    assert result.output["status"] == "success"
    assert "All tasks completed" in result.output["notes"]
```

### Step 2: Run all tests

Run:
```bash
cd modules/tool-pipeline-run && uv run pytest tests/ -v
```
Expected: All tests PASS.

### Step 3: Commit

```bash
git add modules/tool-pipeline-run/tests/test_integration.py
git commit -m "test(tool-pipeline-run): add integration tests for full tool flow"
```

---

## Final File Tree

After all tasks, the new/modified files are:

```
amplifier-bundle-attractor/
├── agents/
│   └── pipeline-runner.yaml             # NEW (Task 7)
├── bundles/
│   └── attractor-interactive.yaml       # NEW (Task 8)
├── context/
│   └── pipeline-awareness.md            # NEW (Task 8)
├── modules/
│   └── tool-pipeline-run/               # NEW (Tasks 1-6)
│       ├── pyproject.toml
│       ├── amplifier_module_tool_pipeline_run/
│       │   └── __init__.py
│       └── tests/
│           ├── __init__.py
│           ├── test_pipeline_run.py
│           └── test_integration.py
└── bundle.md                            # MODIFIED (Task 8)
```

## Complete Method Inventory

`PipelineRunTool` class:
| Method | Task | Purpose |
|--------|------|---------|
| `__init__(config, coordinator)` | 2 | Store config and coordinator reference |
| `input_schema` (property) | 2 | JSON Schema for tool parameters |
| `execute(input)` | 2-6 | Main entry point: validate → resolve → check providers → spawn → result |
| `_resolve_dot_source(dot_file, dot_source)` | 3 | Resolve DOT from file/@mention/inline |
| `_extract_required_providers(dot_source)` | 4 | Parse DOT + stylesheet for provider names |
| `_check_missing_providers(required, available)` | 4 | Set difference of required vs available |
| `_get_available_providers()` | 4 | Read available providers from coordinator config |
| `_show_progress(message)` | 6 | DisplaySystem side-channel message |
| `_emit_event(event_name, data)` | 6 | Hook event emission |

Module-level:
| Function | Task | Purpose |
|----------|------|---------|
| `mount(coordinator, config)` | 2 | Amplifier module entry point |
