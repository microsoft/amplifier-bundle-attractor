"""Pipeline runner tool module for Amplifier.

Exposes a `run_pipeline` tool that lets an interactive agent invoke
DOT graph pipelines at runtime via session.spawn.
"""

# Amplifier module metadata
__amplifier_module_type__ = "tool"

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

__all__ = ["PipelineRunTool", "mount"]

logger = logging.getLogger(__name__)

# Optional import of loop-pipeline for provider validation
try:
    from amplifier_module_loop_pipeline.dot_parser import parse_dot  # type: ignore[import]

    HAS_PIPELINE = True
except ImportError:
    HAS_PIPELINE = False


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
                "params": {
                    "type": "object",
                    "description": (
                        "Key-value parameters for $param expansion in node prompts. "
                        'Example: {"language": "Python", "framework": "FastAPI"} '
                        "expands $language and $framework in prompts."
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["goal"],
        }

    # -----------------------------------------------------------------
    # DOT source resolution
    # -----------------------------------------------------------------

    def _resolve_dot_file_path(self, dot_file: str) -> Path:
        """Resolve a dot_file reference to an existing Path on disk.

        Handles @mention syntax (resolved via the coordinator's
        mention_resolver capability) and plain filesystem paths. Single site
        for this resolution so both the text-only and directory-aware callers
        below can't drift apart (AGENTS.md S4 partial-coverage-symmetry).

        Raises:
            FileNotFoundError: If the resolved path does not exist.
            ValueError: If @mention path cannot be resolved.
        """
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
                raise FileNotFoundError(f"Could not resolve @mention path: {dot_file}")
            file_path = Path(resolved_path)
        else:
            file_path = Path(dot_file)

        if not file_path.exists():
            raise FileNotFoundError(f"DOT file not found: {file_path}")

        return file_path

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
        text, _source_dir = self._resolve_dot_source_with_dir(dot_file, dot_source)
        return text

    def _resolve_dot_source_with_dir(
        self,
        dot_file: str | None,
        dot_source: str | None,
    ) -> tuple[str, str | None]:
        """Resolve DOT source text plus, when file-backed, its directory.

        The spawned child session runs the mounted PipelineOrchestrator from
        a fresh process, so the file path itself never crosses that boundary
        -- only the resolved text does (via orchestrator_config["dot_source"]).
        Without the directory travelling alongside it, a relative dot_file=
        child reference in the pipeline would resolve under the child
        session's --cwd instead of beside the pipeline that was actually
        invoked (the same bug already fixed for the standalone CLI path and
        the mounted orchestrator's own local dot_file path).

        Returns:
            ``(dot_source, source_dir)``. ``source_dir`` is ``None`` when the
            source is inline ``dot_source`` -- there is no file, so there is
            no directory to claim.

        Raises:
            FileNotFoundError: If dot_file path does not exist.
            ValueError: If @mention path cannot be resolved, or neither
                dot_file nor dot_source is provided.
        """
        # Priority 1: inline source
        if dot_source:
            return dot_source, None

        # Priority 2: file path (supports @mention syntax)
        if not dot_file:
            raise ValueError("Either dot_file or dot_source must be provided")

        file_path = self._resolve_dot_file_path(dot_file)
        return file_path.read_text(), str(file_path.resolve().parent)

    # -----------------------------------------------------------------
    # Provider validation
    # -----------------------------------------------------------------

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
        providers: set[str] = set()

        if HAS_PIPELINE:
            graph = parse_dot(dot_source)  # type: ignore[possibly-unbound]

            # Source 1: model_stylesheet rules — use regex since parse_stylesheet
            # may not expose a .properties dict with llm_provider keys
            if graph.model_stylesheet:
                for m in re.finditer(
                    r"llm_provider\s*:\s*([a-zA-Z0-9_-]+)", graph.model_stylesheet
                ):
                    providers.add(m.group(1))

            # Source 2: explicit node attributes
            structural_shapes = {"Mdiamond", "Msquare", "point"}
            for node in graph.nodes.values():
                if node.shape in structural_shapes:
                    continue
                provider = node.attrs.get("llm_provider")
                if provider:
                    providers.add(provider)
        else:
            # Regex fallback when loop-pipeline is not installed
            logger.debug(
                "loop-pipeline not available; using regex fallback for provider extraction"
            )

            # Source 1: stylesheet declarations — llm_provider: value;
            for m in re.finditer(r"llm_provider\s*:\s*([a-zA-Z0-9_-]+)", dot_source):
                providers.add(m.group(1))

            # Source 2: node attribute declarations — llm_provider="value"
            for m in re.finditer(r'llm_provider\s*=\s*"([a-zA-Z0-9_-]+)"', dot_source):
                providers.add(m.group(1))

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
            available.add(agent_name)

        return available

    # -----------------------------------------------------------------
    # Progress reporting
    # -----------------------------------------------------------------

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

    # -----------------------------------------------------------------
    # Main execution
    # -----------------------------------------------------------------

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
                        "Provide a path to a .dot file or an inline DOT "
                        "digraph string."
                    )
                },
            )

        # --- Resolve DOT source ---
        try:
            dot_source_resolved, dot_source_dir = self._resolve_dot_source_with_dir(
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
            missing = self._check_missing_providers(
                required_providers, available_providers
            )
            if missing:
                return ToolResult(
                    success=False,
                    error={
                        "message": (
                            "Pipeline requires providers not available in "
                            f"this session: {', '.join(sorted(missing))}. "
                            "Available: "
                            f"{', '.join(sorted(available_providers)) or 'none'}. "
                            "Configure the missing providers in the "
                            "pipeline-runner agent."
                        ),
                        "missing_providers": sorted(missing),
                        "available_providers": sorted(available_providers),
                    },
                )

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
                        "environment that supports session spawning "
                        "(e.g. the CLI)."
                    )
                },
            )

        # --- Resolve runner agent name ---
        runner_agent = self.config.get("runner_agent", "attractor-pipeline-runner")

        # --- Build orchestrator config for the child session ---
        orchestrator_config: dict[str, Any] = {
            "dot_source": dot_source_resolved,
        }

        # Forward the directory a file-backed dot_file was read from. The
        # spawned child session only receives resolved TEXT (dot_source
        # above) -- the file path itself never crosses that boundary -- so
        # without this, relative dot_file= children in the pipeline would
        # resolve under the child session's --cwd instead of beside the
        # pipeline actually invoked. PipelineOrchestrator reads this same
        # "source_dir" key when dot_source is inline (see its
        # _resolve_dot_source docstring). None for an inline dot_source
        # input -- there is no file, so there is no directory to forward.
        if dot_source_dir:
            orchestrator_config["source_dir"] = dot_source_dir

        # Forward profiles from our config if present
        profiles = self.config.get("profiles")
        if profiles:
            orchestrator_config["profiles"] = profiles

        # Forward params for $param expansion
        params = input.get("params")
        if params:
            orchestrator_config["params"] = params

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

        # --- Progress: pipeline starting ---
        self._show_progress(f"[PIPELINE] Starting pipeline (runner: {runner_agent})...")
        await self._emit_event(
            "pipeline:tool:start",
            {
                "goal": goal,
                "runner_agent": runner_agent,
                "dot_file": dot_file,
            },
        )

        # --- Execute spawn ---
        start_time = time.monotonic()

        try:
            result = await spawn_fn(**spawn_kwargs)
        except Exception as e:
            logger.warning("Pipeline spawn failed: %s", e)
            await self._emit_event(
                "pipeline:tool:complete",
                {
                    "status": "error",
                    "error": str(e),
                },
            )
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
        session_id = (
            result.get("session_id", "unknown")
            if isinstance(result, dict)
            else "unknown"
        )

        # Try to parse structured outcome from pipeline output
        pipeline_status = "success"
        pipeline_notes = ""
        nodes_completed = 0
        node_statuses: dict[str, str] = {}

        if isinstance(output, str) and output.strip().startswith("{"):
            try:
                parsed = json.loads(output)
                pipeline_status = parsed.get("status", "success")
                pipeline_notes = parsed.get("notes") or ""
                nodes_completed = parsed.get("nodes_completed", 0)
                node_statuses = parsed.get("node_statuses", {})
            except (json.JSONDecodeError, AttributeError):
                pipeline_notes = output[:500] if output else ""
        else:
            # Plain text output — use as notes
            pipeline_notes = output[:500] if output else ""

        # Synthesize a summary if notes are still empty
        if not pipeline_notes.strip():
            summary_parts = [f"Pipeline finished with status: {pipeline_status}."]
            if nodes_completed:
                summary_parts.append(f"{nodes_completed} nodes executed.")
            if node_statuses:
                summary_parts.append(
                    "Node results: "
                    + ", ".join(f"{k}={v}" for k, v in node_statuses.items())
                )
            pipeline_notes = " ".join(summary_parts)

        # --- Progress: pipeline complete ---
        self._show_progress(
            f"[PIPELINE] Pipeline complete: {pipeline_status} ({duration}s)"
        )
        await self._emit_event(
            "pipeline:tool:complete",
            {
                "status": pipeline_status,
                "session_id": session_id,
                "duration_seconds": duration,
                "notes": pipeline_notes,
            },
        )

        return ToolResult(
            success=True,
            output={
                "status": pipeline_status,
                "session_id": session_id,
                "notes": pipeline_notes,
                "duration_seconds": duration,
                "nodes_completed": nodes_completed,
                "runner_agent": runner_agent,
                "message": (
                    "Pipeline execution complete. The pipeline has finished "
                    "synchronously — no further action is needed for this pipeline."
                ),
            },
        )


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the run_pipeline tool."""
    config = config or {}
    tool = PipelineRunTool(config, coordinator)
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("Mounted run_pipeline tool")
