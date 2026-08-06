set -euo pipefail

# ---------------------------------------------------------------------------
# Verify: spawned agent's working_dir falls back to os.getcwd() on spawn path
#
# The defect: when a box (LLM/agent) node is driven by pipeline-runner, the
# orchestrator_config dict that backend.py passes at spawn time never contains
# a "working_dir" key. AgentSession._build_system_prompt_text() therefore
# falls back to os.getcwd() (the runner process's own cwd) for the Layer-2
# environment context and project-doc discovery, even though the pipeline's
# intended working directory (context.target_dir / --cwd) differs.
#
# Red  (exit 1): system prompt reports process cwd instead of intended cwd
# Green (exit 0): system prompt reports the intended cwd (defect not present)
# ---------------------------------------------------------------------------

REPO_ROOT="$(pwd)"
MODULE_DIR="${REPO_ROOT}/modules/loop-agent"

# --- infrastructure guards --------------------------------------------------

if [ ! -d "${MODULE_DIR}" ]; then
    echo "INFRA ERROR: modules/loop-agent not found under ${REPO_ROOT}" >&2
    exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "INFRA ERROR: uv not found on PATH" >&2
    exit 2
fi

if [ ! -f "${MODULE_DIR}/pyproject.toml" ]; then
    echo "INFRA ERROR: ${MODULE_DIR}/pyproject.toml not found" >&2
    exit 2
fi

# Sync the module environment (idempotent)
cd "${MODULE_DIR}" || { echo "INFRA ERROR: cannot cd to ${MODULE_DIR}" >&2; exit 2; }

uv sync --quiet 2>/dev/null || {
    echo "INFRA ERROR: uv sync failed in ${MODULE_DIR}" >&2
    exit 2
}

# Confirm the module is importable
uv run python3 -c "import amplifier_module_loop_agent" 2>/dev/null || {
    echo "INFRA ERROR: amplifier_module_loop_agent not importable after uv sync" >&2
    exit 2
}

# --- assertion ---------------------------------------------------------------
# Write the probe to a temp file and run it, capturing output and exit code
# without letting set -e fire on the probe's non-zero exit.
#
# The probe:
#   1. Constructs an AgentOrchestrator with NO "working_dir" in orchestrator_config
#      (exactly as backend.py does on the spawn path today).
#   2. Patches os.getcwd() at the os-module level so the process cwd is a distinct
#      sentinel, making the fallback observable regardless of where the test runner
#      happens to be.
#   3. Calls execute() and inspects the "Working directory:" line in the resulting
#      system prompt.
#   4. Exits 1 (with the red_signal substring) if the system prompt contains the
#      process-cwd sentinel (bug present); exits 0 if it does not (fixed).
#
# GREEN binding: we assert on the OBSERVED "Working directory:" value in the
# system prompt -- the end-state behavior the issue describes -- not on any
# internal function name, argument shape, or code path the fix might take.
# A fix via backend.py (inject working_dir into orchestrator_config) and a fix
# via loop-agent (read from coordinator capability before falling back) both
# prevent the process-cwd sentinel from appearing in the system prompt, so
# both turn this script green.

PROBE_SCRIPT="$(mktemp /tmp/verify_probe_XXXXXX.py)"
PROBE_OUT="$(mktemp /tmp/verify_out_XXXXXX.txt)"
trap 'rm -f "${PROBE_SCRIPT}" "${PROBE_OUT}"' EXIT

cat > "${PROBE_SCRIPT}" <<'PYEOF'
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

try:
    from amplifier_core.message_models import ChatResponse, Usage
    from amplifier_module_loop_agent import AgentOrchestrator
except ImportError as e:
    print(f"INFRA ERROR: import failed: {e}", file=sys.stderr)
    sys.exit(2)

INTENDED_CWD = "/intended/pipeline/cwd"
PROCESS_CWD  = "/fake/process/cwd"

def _text_response(text):
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )

async def probe():
    provider = AsyncMock()
    provider.complete = AsyncMock(return_value=_text_response("done"))

    hooks = MagicMock()
    async def _emit(event, data):
        return MagicMock(action="continue")
    hooks.emit = AsyncMock(side_effect=_emit)

    coordinator = MagicMock()
    coordinator.register_capability = MagicMock()

    # Simulate the pipeline-runner spawn path:
    # orchestrator_config has no "working_dir" key -- backend.py never adds it.
    orch = AgentOrchestrator(
        coordinator=coordinator,
        config={"max_tool_rounds_per_input": 1},  # working_dir intentionally absent
    )

    # Patch os.getcwd at the os-module level.  agent_session.py does
    # `import os` inside _build_system_prompt_text(), which returns the already-
    # cached sys.modules["os"] object, so patching os.getcwd here affects it.
    original_getcwd = os.getcwd
    os.getcwd = lambda: PROCESS_CWD
    try:
        await orch.execute("hi", MagicMock(), {"anthropic": provider}, {}, hooks)
    finally:
        os.getcwd = original_getcwd

    if provider.complete.call_count == 0:
        print("INFRA ERROR: provider.complete was never called", file=sys.stderr)
        sys.exit(2)

    request = provider.complete.call_args[0][0]
    system_content = request.messages[0].content

    # Extract the Working directory line for diagnostics
    wd_line = ""
    for line in system_content.split("\n"):
        if "Working directory:" in line:
            wd_line = line.strip()
            break

    if not wd_line:
        print("INFRA ERROR: no 'Working directory:' line found in system prompt", file=sys.stderr)
        sys.exit(2)

    # The defect: system prompt reports the PROCESS cwd (os.getcwd() fallback)
    # instead of the intended pipeline cwd.
    if PROCESS_CWD in wd_line:
        # Bug is present: the agent's declared working directory is the runner's
        # process cwd, not the pipeline's intended working directory.
        print(f"working_dir falls back to os.getcwd(): {wd_line}")
        sys.exit(1)

    # Green: the system prompt does NOT contain the process-cwd sentinel.
    sys.exit(0)

asyncio.run(probe())
PYEOF

# Run the probe; capture exit code without letting set -e fire on rc=1.
# stderr is suppressed (uv emits a venv-mismatch warning we don't need).
PROBE_RC=0
uv run python3 "${PROBE_SCRIPT}" > "${PROBE_OUT}" 2>/dev/null || PROBE_RC=$?

# rc=2 means an infrastructure problem inside the probe
if [ "${PROBE_RC}" -eq 2 ]; then
    echo "INFRA ERROR: probe script reported an infrastructure problem" >&2
    cat "${PROBE_OUT}" >&2
    exit 2
fi

if [ "${PROBE_RC}" -eq 1 ]; then
    # Defect confirmed -- print the red_signal substring so the caller can grep it
    cat "${PROBE_OUT}"
    exit 1
fi

# exit 0: defect not present (already fixed or not triggered on this commit)
exit 0
