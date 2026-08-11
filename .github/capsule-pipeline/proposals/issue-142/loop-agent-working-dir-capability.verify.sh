set -euo pipefail

# ---------------------------------------------------------------------------
# Gate: loop-agent working_dir capability propagation
#
# Checks that AgentOrchestrator.execute() honours the coordinator's
# session.working_dir capability for BOTH consumers:
#   (a) the Working directory: line in the environment context
#   (b) project-doc discovery (discover_project_docs / AGENTS.md inclusion)
#
# RED signal (exit 1): "working_dir capability not propagated to agent session"
# GREEN (exit 0):      capability value reaches both consumers correctly
# INFRA (exit 2):      missing tooling or repo structure problem
# ---------------------------------------------------------------------------

REPO_ROOT="$(pwd)"
MODULE_DIR="$REPO_ROOT/modules/loop-agent"

# --- infrastructure guards --------------------------------------------------

if ! command -v python3 >/dev/null 2>&1; then
    echo "INFRA: python3 not found on PATH" >&2
    exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "INFRA: uv not found on PATH" >&2
    exit 2
fi

if [ ! -d "$MODULE_DIR" ]; then
    echo "INFRA: modules/loop-agent not found under $REPO_ROOT" >&2
    exit 2
fi

if [ ! -f "$MODULE_DIR/pyproject.toml" ]; then
    echo "INFRA: modules/loop-agent/pyproject.toml not found" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Part 1: Run the existing on-topic regression test (test_system_prompt_wiring.py)
# which covers working-directory / system-prompt wiring.
# This folds in shipped coverage and also catches regressions introduced by the fix.
# ---------------------------------------------------------------------------

WIRING_TEST="$MODULE_DIR/tests/test_system_prompt_wiring.py"

if [ -f "$WIRING_TEST" ]; then
    if ! ( cd "$MODULE_DIR" && uv run pytest tests/test_system_prompt_wiring.py -v --tb=short 2>&1 ); then
        echo "working_dir capability not propagated to agent session: test_system_prompt_wiring.py failed after fix"
        exit 1
    fi
fi

# If a fix ships its own regression test (under any filename) in modules/loop-agent/tests/,
# run the full test suite so it is exercised.
# A non-zero exit means a test failed.
if ! ( cd "$MODULE_DIR" && uv run pytest tests/ -v --tb=short 2>&1 ); then
    echo "working_dir capability not propagated to agent session: loop-agent test suite failed"
    exit 1
fi

# ---------------------------------------------------------------------------
# Part 2: Behavioral probe — both consumers — through AgentOrchestrator.execute()
#
# Constructs a temporary workspace, places an AGENTS.md with a runtime-born
# neutral sentinel, and drives AgentOrchestrator.execute() with a coordinator
# that returns the workspace path from get_capability("session.working_dir")
# but with NO working_dir in the config dict.
#
# Asserts:
#   (a) EXACTLY ONE line in the system prompt starts with "Working directory:"
#       and that line EQUALS "Working directory: <capability_path>" — an
#       append-dodge that keeps the real line on os.getcwd() and adds a
#       second, contradictory line fails on both arms (equality + uniqueness)
#   (b) the AGENTS.md sentinel from the capability directory appears in
#       the system prompt (discover_project_docs walked the right root)
#
# Also runs a mixed-scope check: a second session in the SAME probe run
# with NO capability and NO configured working_dir must not crash and
# must have the exact "Working directory: <process_cwd>" line (fallback preserved).
# ---------------------------------------------------------------------------

# Generate runtime-born neutral names — no domain vocabulary.
RAND_A="$RANDOM$RANDOM"
RAND_B="$RANDOM$RANDOM"
SENTINEL_CONTENT="vrfxq${RAND_A}zplm"
NEUTRAL_PROBE_ID="nxkwp${RAND_B}jrtb"

PROBE_SCRIPT=$(mktemp /tmp/gate_probe_XXXXXX.py)
trap 'rm -f "$PROBE_SCRIPT"' EXIT

cat > "$PROBE_SCRIPT" << PYEOF
import asyncio
import os
import sys
import tempfile

# Resolve imports from the invoking repo tree, never from ambient site-packages.
sys.path.insert(0, "$MODULE_DIR")
sys.path.insert(0, "$MODULE_DIR/../unified-llm-client")

try:
    from unittest.mock import AsyncMock, MagicMock
    from amplifier_module_loop_agent import AgentOrchestrator
    from amplifier_core.message_models import ChatResponse, Usage
except Exception as e:
    print(f"INFRA: import failed: {e}", file=sys.stderr)
    sys.exit(2)

SENTINEL = "$SENTINEL_CONTENT"
PROBE_ID = "$NEUTRAL_PROBE_ID"

def _text_response(text):
    return ChatResponse(
        content=[{"type": "text", "text": text}],
        tool_calls=None,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
    )

def _make_hooks():
    hooks = MagicMock()
    async def _emit(event, data):
        return MagicMock(action="continue")
    hooks.emit = AsyncMock(side_effect=_emit)
    return hooks

def _assert_exact_wd_line(system_content, expected_path, label):
    """Line-anchored equality + uniqueness for the Working directory: line.

    Exactly ONE line in the system prompt may start with "Working directory:",
    and that line must EQUAL "Working directory: <expected_path>".
    An append-dodge that leaves the real line wrong and adds a second,
    contradictory line fails BOTH arms: the real line mismatches expected,
    and the line count is 2.
    """
    expected_line = f"Working directory: {expected_path}"
    wd_lines = [
        line for line in system_content.splitlines()
        if line.startswith("Working directory:")
    ]
    if len(wd_lines) != 1 or wd_lines[0] != expected_line:
        print(
            f"working_dir capability not propagated to agent session: "
            f"{label}: expected exactly one working-directory line "
            f"'{expected_line}', got {len(wd_lines)} line(s): {wd_lines!r}"
        )
        sys.exit(1)

async def main():
    # -----------------------------------------------------------------------
    # Set up two temp directories:
    #   pipeline_dir — the "pipeline cwd" with an AGENTS.md containing SENTINEL
    #   process_dir  — the "process cwd" with NO AGENTS.md
    # -----------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as pipeline_dir, \
         tempfile.TemporaryDirectory() as process_dir:

        # Write AGENTS.md into pipeline_dir only
        agents_md = os.path.join(pipeline_dir, "AGENTS.md")
        with open(agents_md, "w") as f:
            f.write(f"# Project Rules\n\n{SENTINEL}\n")

        # Change process cwd to process_dir (no AGENTS.md there)
        original_cwd = os.getcwd()
        os.chdir(process_dir)
        try:
            # -------------------------------------------------------------------
            # PROBE A: capability present, no explicit working_dir in config
            # Coordinator returns pipeline_dir from get_capability("session.working_dir")
            # -------------------------------------------------------------------
            provider_a = AsyncMock()
            provider_a.complete = AsyncMock(return_value=_text_response("done"))
            hooks_a = _make_hooks()

            coordinator_a = MagicMock()
            coordinator_a.register_capability = MagicMock()

            def get_cap_a(key):
                if key == "session.working_dir":
                    return pipeline_dir  # the pipeline cwd
                return None

            coordinator_a.get_capability = MagicMock(side_effect=get_cap_a)

            orch_a = AgentOrchestrator(
                coordinator=coordinator_a,
                # No working_dir in config — capability must supply it
                config={"system_prompt": "Base.", "max_tool_rounds_per_input": 1},
            )

            try:
                await orch_a.execute(
                    "hello", MagicMock(), {"anthropic": provider_a}, {}, hooks_a
                )
            except Exception as e:
                print(f"working_dir capability not propagated to agent session: execute() raised unexpectedly: {e}")
                sys.exit(1)

            # Extract system prompt from the ChatRequest sent to the provider
            call_args = provider_a.complete.call_args
            if call_args is None:
                print("working_dir capability not propagated to agent session: provider.complete was never called")
                sys.exit(1)

            request = call_args[0][0]
            system_content = request.messages[0].content

            # (a) Environment context: line-anchored equality + uniqueness.
            # Exactly one "Working directory:" line, and it must equal the
            # capability path. A dodge that keeps the real line pointing at
            # os.getcwd() and APPENDS a second, contradictory line fails here
            # on both arms (mismatched real line, and two matching lines).
            _assert_exact_wd_line(system_content, pipeline_dir, "capability path")

            # (b) Project-doc discovery: SENTINEL from pipeline_dir/AGENTS.md must appear
            if SENTINEL not in system_content:
                print(
                    f"working_dir capability not propagated to agent session: "
                    f"AGENTS.md sentinel from capability dir not found in system prompt — "
                    f"discover_project_docs walked the wrong root"
                )
                sys.exit(1)

            # -------------------------------------------------------------------
            # PROBE B (mixed-scope): no capability, no explicit working_dir
            # Session must still build and use os.getcwd() (fallback preserved).
            # The exact "Working directory: <process_dir>" line must appear.
            # -------------------------------------------------------------------
            provider_b = AsyncMock()
            provider_b.complete = AsyncMock(return_value=_text_response("done"))
            hooks_b = _make_hooks()

            coordinator_b = MagicMock()
            coordinator_b.register_capability = MagicMock()
            coordinator_b.get_capability = MagicMock(return_value=None)

            orch_b = AgentOrchestrator(
                coordinator=coordinator_b,
                config={"system_prompt": "Base.", "max_tool_rounds_per_input": 1},
            )

            try:
                await orch_b.execute(
                    "hello", MagicMock(), {"anthropic": provider_b}, {}, hooks_b
                )
            except Exception as e:
                print(f"working_dir capability not propagated to agent session: fallback path raised unexpectedly: {e}")
                sys.exit(1)

            # Fallback must have used process_dir (os.getcwd())
            call_args_b = provider_b.complete.call_args
            if call_args_b is None:
                print("working_dir capability not propagated to agent session: fallback provider.complete was never called")
                sys.exit(1)

            request_b = call_args_b[0][0]
            system_content_b = request_b.messages[0].content

            # process_dir is current cwd — fallback must name it in the
            # Working directory: line (same equality + uniqueness discipline).
            _assert_exact_wd_line(system_content_b, process_dir, "fallback path")

            # Doc-discovery scope: no capability was offered on this session,
            # so the sentinel from pipeline_dir/AGENTS.md must NOT appear —
            # nothing may inject docs from a directory this session was never
            # pointed at.
            if SENTINEL in system_content_b:
                print(
                    f"working_dir capability not propagated to agent session: "
                    f"fallback path: sentinel from the capability directory "
                    f"leaked into a session with no capability — doc "
                    f"discovery is not scoped to the resolved working dir"
                )
                sys.exit(1)

            # -------------------------------------------------------------------
            # PROBE C: explicit working_dir in config wins over capability
            # -------------------------------------------------------------------
            with tempfile.TemporaryDirectory() as explicit_dir:
                explicit_agents = os.path.join(explicit_dir, "AGENTS.md")
                with open(explicit_agents, "w") as f:
                    f.write(f"# Explicit Rules\n\n{PROBE_ID}\n")

                provider_c = AsyncMock()
                provider_c.complete = AsyncMock(return_value=_text_response("done"))
                hooks_c = _make_hooks()

                coordinator_c = MagicMock()
                coordinator_c.register_capability = MagicMock()

                def get_cap_c(key):
                    if key == "session.working_dir":
                        return pipeline_dir  # capability points elsewhere
                    return None

                coordinator_c.get_capability = MagicMock(side_effect=get_cap_c)

                orch_c = AgentOrchestrator(
                    coordinator=coordinator_c,
                    # explicit working_dir must win over capability
                    config={
                        "system_prompt": "Base.",
                        "max_tool_rounds_per_input": 1,
                        "working_dir": explicit_dir,
                    },
                )

                try:
                    await orch_c.execute(
                        "hello", MagicMock(), {"anthropic": provider_c}, {}, hooks_c
                    )
                except Exception as e:
                    print(f"working_dir capability not propagated to agent session: explicit-wins path raised: {e}")
                    sys.exit(1)

                call_args_c = provider_c.complete.call_args
                if call_args_c is None:
                    print("working_dir capability not propagated to agent session: explicit-wins provider.complete not called")
                    sys.exit(1)

                request_c = call_args_c[0][0]
                system_content_c = request_c.messages[0].content

                # Explicit dir must win — same equality + uniqueness discipline.
                _assert_exact_wd_line(
                    system_content_c, explicit_dir, "explicit-wins path"
                )

                if PROBE_ID not in system_content_c:
                    print(
                        f"working_dir capability not propagated to agent session: "
                        f"AGENTS.md from explicit_dir not found — discover_project_docs used wrong root"
                    )
                    sys.exit(1)

                # Doc-discovery scope: explicit working_dir won, so docs must
                # come ONLY from explicit_dir. A dodge that walks BOTH roots
                # (appending the capability dir's docs as well) carries the
                # capability-dir sentinel here and fails.
                if SENTINEL in system_content_c:
                    print(
                        f"working_dir capability not propagated to agent session: "
                        f"explicit-wins path: capability-dir sentinel present "
                        f"alongside explicit-dir docs — doc discovery walked "
                        f"more than the winning root"
                    )
                    sys.exit(1)

        finally:
            os.chdir(original_cwd)

    print("GATE PASS: working_dir capability propagated correctly to both consumers")
    sys.exit(0)

asyncio.run(main())
PYEOF

# Run the probe via uv so deps are resolved from the module's own project.
# We pass the script to uv run python so it uses the module's environment.
# Capture the probe's real exit code. (Inside an `if ! cmd` body, $? is the
# already-negated condition result — always 0 — so the previous INFRA branch
# here was dead code and probe-setup failures were misreported as RED rc=1.)
set +e
( cd "$MODULE_DIR" && uv run python "$PROBE_SCRIPT" 2>&1 )
PROBE_RC=$?
set -e
if [ "$PROBE_RC" -ge 2 ]; then
    # uv failure or probe import/setup failure — infrastructure, not behavior.
    echo "INFRA: probe script exited with rc=$PROBE_RC (uv or import failure)" >&2
    exit 2
elif [ "$PROBE_RC" -ne 0 ]; then
    # The probe already printed the red_signal diagnostic on stdout.
    exit 1
fi
