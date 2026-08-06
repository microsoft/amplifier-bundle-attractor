set -euo pipefail

# ---------------------------------------------------------------------------
# DEFINITION.verify.sh
#
# Checks whether the pipeline backend forwards the pipeline's working directory
# (context.target_dir) into the orchestrator_config it passes to the spawn
# function when executing a box (LLM/agent) node.
#
# Exit codes:
#   0  - defect NOT present (working_dir is correctly forwarded)
#   1  - defect IS present (working_dir missing from orchestrator_config)
#   2+ - infrastructure/prerequisite failure (not an assertion failure)
# ---------------------------------------------------------------------------

# --- Prerequisites ---

command -v python3 >/dev/null 2>&1 || { echo "INFRA: python3 not found" >&2; exit 2; }

LOOP_PIPELINE_DIR="modules/loop-pipeline"
if [ ! -d "$LOOP_PIPELINE_DIR" ]; then
    echo "INFRA: expected directory $LOOP_PIPELINE_DIR not found (run from repo root)" >&2
    exit 2
fi

BACKEND_PY="$LOOP_PIPELINE_DIR/amplifier_module_loop_pipeline/backend.py"
if [ ! -f "$BACKEND_PY" ]; then
    echo "INFRA: $BACKEND_PY not found" >&2
    exit 2
fi

command -v uv >/dev/null 2>&1 || { echo "INFRA: uv not found" >&2; exit 2; }

# Ensure the loop-pipeline venv is ready (idempotent)
(cd "$LOOP_PIPELINE_DIR" && uv sync --quiet 2>/dev/null) || {
    echo "INFRA: uv sync failed in $LOOP_PIPELINE_DIR" >&2
    exit 2
}

# ---------------------------------------------------------------------------
# Write the behavioral check to a temp file so we can capture both its
# stdout and its exit code cleanly under set -e.
#
# The check:
#   1. Constructs an AmplifierBackend with a mock coordinator that records
#      the kwargs passed to the spawn function.
#   2. Calls backend.run() with a PipelineContext that has context.target_dir
#      set to a known sentinel path.
#   3. Inspects the orchestrator_config in the recorded spawn kwargs.
#   4. Exits 1 (printing the red signal) if working_dir is absent or wrong.
#      Exits 0 if working_dir is correctly forwarded.
#
# RED  (exit 1): working_dir is absent from orchestrator_config — the spawned
#                agent would fall back to os.getcwd() for its system-prompt
#                environment context and project-doc discovery.
# GREEN (exit 0): working_dir is present and correct — the spawned agent's
#                 declared working directory matches the pipeline's --cwd.
# ---------------------------------------------------------------------------

TMPPY=$(mktemp /tmp/verify_working_dir_XXXXXX.py)
# shellcheck disable=SC2064
trap "rm -f '$TMPPY'" EXIT

cat > "$TMPPY" << 'PYEOF'
import sys
import types
import asyncio
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Minimal stubs so backend.py imports succeed without the full foundation stack
# ---------------------------------------------------------------------------

if "amplifier_core" not in sys.modules:
    @dataclass
    class _StubMessage:
        role: str = "user"
        content: Any = ""
        tool_call_id: str | None = None
        name: str | None = None
        metadata: dict | None = None

    @dataclass
    class _StubToolCallBlock:
        id: str = ""
        name: str = ""
        input: dict = field(default_factory=dict)
        type: str = "tool_call"

    @dataclass
    class _StubChatRequest:
        messages: list = field(default_factory=list)
        tools: list | None = None
        tool_choice: str | None = None
        reasoning_effort: str | None = None

    _stub_core = types.ModuleType("amplifier_core")
    _stub_core.Message = _StubMessage  # type: ignore[attr-defined]
    _stub_core.ChatRequest = _StubChatRequest  # type: ignore[attr-defined]
    sys.modules["amplifier_core"] = _stub_core

    _stub_msg = types.ModuleType("amplifier_core.message_models")
    _stub_msg.ToolCallBlock = _StubToolCallBlock  # type: ignore[attr-defined]
    sys.modules["amplifier_core.message_models"] = _stub_msg

if "amplifier_foundation" not in sys.modules:
    @dataclass
    class _StubProviderPreference:
        provider: str = ""
        model: str = ""

    _stub_foundation = types.ModuleType("amplifier_foundation")
    _stub_foundation.ProviderPreference = _StubProviderPreference  # type: ignore[attr-defined]
    sys.modules["amplifier_foundation"] = _stub_foundation

# ---------------------------------------------------------------------------
# Import the real production code under test
# ---------------------------------------------------------------------------
from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Node


# ---------------------------------------------------------------------------
# Mock coordinator that records exactly what the spawn function receives
# ---------------------------------------------------------------------------

class _MockSession:
    config: dict = {}


class _RecordingCoordinator:
    """Coordinator whose spawn function records the kwargs it receives."""

    def __init__(self) -> None:
        self.last_spawn_kwargs: dict[str, Any] = {}
        self.session = _MockSession()
        # Minimal agent config that satisfies the recursion guard in backend.py:
        # the agent must declare session.orchestrator.module != "loop-pipeline".
        self.config: dict[str, Any] = {
            "agents": {
                "attractor-anthropic": {
                    "session": {"orchestrator": {"module": "loop-agent"}},
                }
            }
        }

    def get_capability(self, name: str) -> Any:
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs: Any) -> dict[str, Any]:
        self.last_spawn_kwargs = kwargs
        return {"output": "done", "session_id": "child-1"}


# ---------------------------------------------------------------------------
# The behavioral check
# ---------------------------------------------------------------------------

INTENDED_DIR = "/intended/target/dir"


async def _check() -> None:
    coordinator = _RecordingCoordinator()
    backend = AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )
    node = Node(id="implement", prompt="Build it", attrs={"llm_provider": "anthropic"})

    ctx = PipelineContext()
    ctx.set("context.target_dir", INTENDED_DIR)

    await backend.run(node, "task", ctx)

    orch_config: dict[str, Any] = coordinator.last_spawn_kwargs.get(
        "orchestrator_config", {}
    )
    actual_working_dir = orch_config.get("working_dir")

    if actual_working_dir != INTENDED_DIR:
        # Defect is present: working_dir was not forwarded into orchestrator_config.
        # The spawned agent will fall back to os.getcwd() for its environment
        # context and project-doc discovery, silently reporting the wrong directory.
        print(
            "working_dir missing from orchestrator_config passed to spawn"
            f" (got {actual_working_dir!r}, expected {INTENDED_DIR!r})"
        )
        sys.exit(1)

    # Defect is not present: working_dir is correctly forwarded.
    sys.exit(0)


asyncio.run(_check())
PYEOF

# Run the check inside the loop-pipeline module's own venv (matches CI isolation)
set +e
OUTPUT=$(cd "$LOOP_PIPELINE_DIR" && uv run python3 "$TMPPY" 2>/dev/null)
RC=$?
set -e

if [ $RC -eq 1 ]; then
    # Assertion failure: the defect is reproducing.
    echo "$OUTPUT"
    exit 1
elif [ $RC -ne 0 ]; then
    echo "INFRA: Python check exited with unexpected code $RC" >&2
    if [ -n "$OUTPUT" ]; then
        echo "$OUTPUT" >&2
    fi
    exit 2
fi

# Defect not present (exit 0 from Python means working_dir is correctly forwarded)
exit 0
