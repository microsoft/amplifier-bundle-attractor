set -euo pipefail

# ---------------------------------------------------------------------------
# Gate: spawned agent's report_outcome verdict must be honored when the child
# produces non-empty prose output.
#
# Defect: _run_with_spawn in backend.py only consults metadata.report_outcome
# when output.strip() is empty. Non-empty prose causes the metadata to be
# silently discarded, returning is_explicit=False, preferred_label=None.
#
# Exit codes:
#   0  -- defect is NOT present (already fixed or gate does not capture it)
#   1  -- defect IS present (assertion failure; prints red_signal substring)
#  >=2 -- infrastructure problem (missing tooling, cannot run)
#
# VOID DODGE DEFENSE: probes are standalone Python scripts run via `python3`,
# NOT pytest test files. They are written to a tmpdir outside both test trees,
# so no pytest conftest.py hook (pytest_runtest_makereport or otherwise) can
# intercept or forge their results. The shell script observes the exit code
# and stdout directly.
# ---------------------------------------------------------------------------

# --- Infrastructure guards --------------------------------------------------

command -v python3 >/dev/null 2>&1 || { echo "INFRA: python3 not found"; exit 2; }
command -v uv >/dev/null 2>&1 || { echo "INFRA: uv not found"; exit 2; }

REPO_ROOT="$(pwd)"

LOOP_MODULE_DIR="modules/loop-pipeline"
RUNNER_MODULE_DIR="modules/pipeline-runner"

[ -d "$LOOP_MODULE_DIR" ] || { echo "INFRA: $LOOP_MODULE_DIR not found in $(pwd)"; exit 2; }
[ -f "$LOOP_MODULE_DIR/pyproject.toml" ] || { echo "INFRA: $LOOP_MODULE_DIR/pyproject.toml not found"; exit 2; }
[ -d "$RUNNER_MODULE_DIR" ] || { echo "INFRA: $RUNNER_MODULE_DIR not found in $(pwd)"; exit 2; }
[ -f "$RUNNER_MODULE_DIR/pyproject.toml" ] || { echo "INFRA: $RUNNER_MODULE_DIR/pyproject.toml not found"; exit 2; }

BACKEND_PY="$LOOP_MODULE_DIR/amplifier_module_loop_pipeline/backend.py"
[ -f "$BACKEND_PY" ] || { echo "INFRA: $BACKEND_PY not found"; exit 2; }

RUNNER_PY="$RUNNER_MODULE_DIR/amplifier_module_pipeline_runner/runner.py"
[ -f "$RUNNER_PY" ] || { echo "INFRA: $RUNNER_PY not found"; exit 2; }

# --- Generate runtime-random identifiers ------------------------------------
# Names use only alphanumerics -- no domain vocabulary from the issue.
# Runtime-born so no hardcode can enumerate them.
RAND_LABEL="lbl${RANDOM}x${RANDOM}"
RAND_NOTES="nts${RANDOM}z${RANDOM}"
RAND_SUFFIX="${RANDOM}_${RANDOM}"

# --- Probe directory: OUTSIDE both test trees -------------------------------
# Written to a tmpdir so no pytest conftest.py in either module's tests/
# directory can intercept execution via pytest_runtest_makereport or any
# other hook -- these are standalone Python scripts, not pytest test files.
PROBE_DIR="$(mktemp -d)"

PROBE1_PY="$PROBE_DIR/probe1_backend_${RAND_SUFFIX}.py"
PROBE2_PY="$PROBE_DIR/probe2_transport_${RAND_SUFFIX}.py"
PROBE1_OUT="$PROBE_DIR/probe1.out"
PROBE2_OUT="$PROBE_DIR/probe2.out"

# Ensure cleanup on exit
cleanup() {
    rm -rf "$PROBE_DIR"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Probe 1: backend standalone round-trip (loop-pipeline)
#
# Directly invokes AmplifierBackend.run() with asyncio. No pytest involved.
# Generates a runtime-random expected label, drives it through the reported
# path, and exits 1 with the red_signal substring if the recovered value
# does not round-trip equal to the expected one.
#
# Covers:
#   a) Non-empty prose + metadata.report_outcome -> metadata must win (primary
#      regression: the runtime-random label must be recovered exactly)
#   b) Mixed: one call with metadata (label must round-trip), one without
#      (prose fallback path must still work) -- per-call, not whole-scope
#   c) Non-empty JSON fail output without metadata -> _parse_outcome path
#      (kills the if-True void dodge: always calling _outcome_from_spawn_result
#       returns SUCCESS from the spawn envelope, but _parse_outcome returns FAIL
#       from the JSON -- the two paths diverge here)
#
# HERMETICITY: sys.path.insert(0, LOOP_SRC_DIR) before any import ensures
# the pinned source tree's backend is resolved, not any installed copy.
# ---------------------------------------------------------------------------

LOOP_SRC_DIR="$REPO_ROOT/$LOOP_MODULE_DIR"

cat > "$PROBE1_PY" << PYEOF
"""Standalone gate probe 1: AmplifierBackend.run() round-trip.

Run via: python3 <this_file>
NOT a pytest test file -- runs outside any test tree so no conftest.py
hook can intercept execution.

Exit 0 = defect not present.
Exit 1 = defect present (prints red_signal substring).
Exit 2 = infrastructure problem.
"""
import asyncio
import sys
import types
from dataclasses import dataclass, field
from typing import Any

# HERMETICITY: resolve loop-pipeline from the invoking source tree.
_LOOP_SRC = "${LOOP_SRC_DIR}"
if _LOOP_SRC not in sys.path:
    sys.path.insert(0, _LOOP_SRC)

# Provide amplifier_core stub (mirrors conftest.py pattern).
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

try:
    from amplifier_module_loop_pipeline.backend import AmplifierBackend
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.graph import Node
    from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
except Exception as exc:
    print(f"INFRA: could not import loop-pipeline modules: {exc}", file=sys.stderr)
    sys.exit(2)

# Runtime-random expected values (injected from gate script at write time).
EXPECTED_LABEL = "${RAND_LABEL}"
EXPECTED_NOTES = "${RAND_NOTES}"


class _MockSession:
    config: dict = {}


class _MockCoordinator:
    def __init__(self, spawn_result: dict) -> None:
        self._spawn_result = spawn_result
        self.session = _MockSession()
        self.config: dict = {
            "agents": {
                "attractor-anthropic": {
                    "session": {"orchestrator": {"module": "loop-agent"}}
                }
            }
        }

    def get_capability(self, name: str):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs: Any) -> dict:
        return self._spawn_result


def _make_backend(spawn_result: dict) -> AmplifierBackend:
    return AmplifierBackend(
        coordinator=_MockCoordinator(spawn_result),
        profiles={"anthropic": "attractor-anthropic"},
    )


def _make_node(node_id: str = "n1") -> Node:
    return Node(id=node_id, prompt="do work", attrs={"llm_provider": "anthropic"})


async def _run_all() -> None:
    # ------------------------------------------------------------------
    # Assertion A: Non-empty prose + metadata.report_outcome with a
    # runtime-random preferred_label. The metadata MUST win (spec §35).
    # Positive round-trip: the runtime-random label must be recovered exactly.
    # ------------------------------------------------------------------
    spawn_result_a = {
        "output": "The work is complete and everything looks good.",  # non-empty
        "session_id": "s-probe-a",
        "status": "success",
        "metadata": {
            "report_outcome": {
                "status": "success",
                "preferred_label": EXPECTED_LABEL,
                "notes": EXPECTED_NOTES,
            }
        },
    }
    try:
        result_a = await _make_backend(spawn_result_a).run(
            _make_node("na"), "classify", PipelineContext()
        )
    except Exception as exc:
        print(
            f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
            f"backend.run() raised unexpectedly on non-empty prose path: {exc}"
        )
        sys.exit(1)

    if not isinstance(result_a, Outcome):
        print(
            f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
            f"backend.run() did not return an Outcome (got {type(result_a).__name__!r})"
        )
        sys.exit(1)

    if result_a.preferred_label != EXPECTED_LABEL:
        print(
            f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
            f"expected preferred_label={EXPECTED_LABEL!r}, "
            f"got {result_a.preferred_label!r}"
        )
        sys.exit(1)

    if result_a.is_explicit is not True:
        print(
            f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
            f"expected is_explicit=True, got is_explicit={result_a.is_explicit!r}"
        )
        sys.exit(1)

    print(f"Probe1-A PASSED: preferred_label={result_a.preferred_label!r}, is_explicit={result_a.is_explicit}")

    # ------------------------------------------------------------------
    # Assertion B: Mixed probe -- call WITH metadata (must round-trip),
    # call WITHOUT metadata (prose fallback, is_explicit must be False).
    # Kills whole-scope suppression: an early-return that silences metadata
    # lookup for the entire session would green A but fail here.
    # ------------------------------------------------------------------
    result_b1 = await _make_backend({
        "output": "I finished the analysis.",
        "session_id": "s-b1",
        "status": "success",
        "metadata": {
            "report_outcome": {
                "status": "success",
                "preferred_label": EXPECTED_LABEL,
            }
        },
    }).run(_make_node("nb1"), "task", PipelineContext())

    if result_b1.preferred_label != EXPECTED_LABEL:
        print(
            f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
            f"mixed probe call-with-metadata: expected {EXPECTED_LABEL!r}, "
            f"got {result_b1.preferred_label!r}"
        )
        sys.exit(1)
    if result_b1.is_explicit is not True:
        print(
            f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
            f"mixed probe call-with-metadata: expected is_explicit=True, "
            f"got {result_b1.is_explicit!r}"
        )
        sys.exit(1)

    result_b2 = await _make_backend({
        "output": "The work is done.",
        "session_id": "s-b2",
        "status": "success",
        "metadata": {},
    }).run(_make_node("nb2"), "task", PipelineContext())

    if result_b2.status != StageStatus.SUCCESS:
        print(
            f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
            f"mixed probe call-without-metadata: expected SUCCESS from prose, "
            f"got {result_b2.status!r}"
        )
        sys.exit(1)
    if result_b2.is_explicit is not False:
        print(
            f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
            f"mixed probe call-without-metadata: expected is_explicit=False "
            f"(prose-derived), got {result_b2.is_explicit!r}"
        )
        sys.exit(1)

    print(f"Probe1-B PASSED: per-call behavior confirmed")

    # ------------------------------------------------------------------
    # Assertion C: Non-empty JSON fail output with NO metadata.report_outcome.
    # _parse_outcome path must be used (not _outcome_from_spawn_result).
    # Kills the if-True void dodge: that patch always calls
    # _outcome_from_spawn_result, which returns SUCCESS from the spawn
    # envelope's status="success" field -- but the correct behavior is
    # _parse_outcome('{"status":"fail"}') -> FAIL. The two paths diverge.
    # ------------------------------------------------------------------
    result_c = await _make_backend({
        "output": '{"status": "fail", "failure_reason": "validation failed"}',
        "session_id": "s-c",
        "status": "success",  # spawn envelope says success -- must NOT win
        "metadata": {},       # no report_outcome
    }).run(_make_node("nc"), "task", PipelineContext())

    if result_c.status != StageStatus.FAIL:
        print(
            f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
            f"non-empty JSON fail output without metadata should yield FAIL via "
            f"_parse_outcome, but got status={result_c.status!r}. "
            f"(A patch that always takes the empty-output path would return SUCCESS "
            f"from the spawn envelope's status field instead.)"
        )
        sys.exit(1)

    print(f"Probe1-C PASSED: JSON fail output without metadata -> FAIL (parse_outcome path)")


asyncio.run(_run_all())
print("Probe 1 PASSED: backend round-trip -- defect is not present")
sys.exit(0)
PYEOF

# ---------------------------------------------------------------------------
# Probe 2: public-surface standalone round-trip (pipeline-runner / drive_engine)
#
# Directly invokes drive_engine() with asyncio. No pytest involved.
# Builds a fake coordinator with session.spawn registered (returning a
# controlled result with metadata.report_outcome), calls drive_engine with a
# minimal single-box DOT graph, and asserts the persisted per-node status.json
# records is_explicit=true and preferred_next_label equal to the runtime-random
# label. This is the exact observable symptom the issue reporter described.
#
# HERMETICITY: sys.path.insert(0, LOOP_SRC_DIR) before any import ensures
# the pinned source tree's backend is resolved, not any installed copy in the
# pipeline-runner venv.
# ---------------------------------------------------------------------------

RUNNER_SRC_DIR="$REPO_ROOT/$RUNNER_MODULE_DIR"
BOX_NODE_ID="bxnd${RAND_SUFFIX}"

cat > "$PROBE2_PY" << PYEOF2
"""Standalone gate probe 2: drive_engine() transport round-trip.

Run via: python3 <this_file>
NOT a pytest test file -- runs outside any test tree so no conftest.py
hook can intercept execution.

Exit 0 = defect not present.
Exit 1 = defect present (prints red_signal substring).
Exit 2 = infrastructure problem.
"""
import asyncio
import json
import os
import sys
import tempfile
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# HERMETICITY: resolve loop-pipeline from the invoking source tree first,
# so the pinned backend is used regardless of what is installed in the
# pipeline-runner venv.
_LOOP_SRC = "${LOOP_SRC_DIR}"
_RUNNER_SRC = "${RUNNER_SRC_DIR}"
if _LOOP_SRC not in sys.path:
    sys.path.insert(0, _LOOP_SRC)
if _RUNNER_SRC not in sys.path:
    sys.path.insert(1, _RUNNER_SRC)

# Provide amplifier_core stub.
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

try:
    from amplifier_module_pipeline_runner.runner import drive_engine
except Exception as exc:
    print(f"INFRA: could not import pipeline-runner modules: {exc}", file=sys.stderr)
    sys.exit(2)

# Runtime-random expected values (injected from gate script at write time).
EXPECTED_LABEL = "${RAND_LABEL}"

# Minimal single-box DOT graph. The node id is runtime-generated and
# semantically neutral.
_BOX_NODE_ID = "${BOX_NODE_ID}"
_DOT_SOURCE = f"""
digraph gate {{
    graph [goal="gate probe"]
    start [shape=Mdiamond]
    {_BOX_NODE_ID} [shape=box, llm_provider="anthropic", prompt="probe task"]
    done [shape=Msquare]
    start -> {_BOX_NODE_ID} -> done
}}
"""

# Set ANTHROPIC_API_KEY for the preflight check (presence only, never validity).
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-gate-probe-fake")


class _StubCoordinator:
    """Coordinator stub that returns a controlled spawn result.

    Mirrors the pattern from test_provider_preflight_drive_engine.py:
    the coordinator has session.spawn registered, a session attribute,
    and config["agents"] with the profile agent name.
    """

    def __init__(self, spawn_result: dict) -> None:
        self._spawn_result = spawn_result
        self.session = None
        self.hooks = None
        self.config: dict[str, Any] = {
            "agents": {
                "attractor-agent-anthropic": {
                    "session": {"orchestrator": {"module": "loop-agent"}},
                },
            }
        }

    def get_capability(self, name: str):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs: Any) -> dict:
        return self._spawn_result


async def _run_probe() -> None:
    spawn_result = {
        "output": "I have completed the task.",  # non-empty prose
        "session_id": "s-transport-gate",
        "status": "success",
        "metadata": {
            "report_outcome": {
                "status": "success",
                "preferred_label": EXPECTED_LABEL,
            }
        },
    }
    coordinator = _StubCoordinator(spawn_result)

    with tempfile.TemporaryDirectory() as tmpdir:
        logs_dir = Path(tmpdir) / "logs"
        logs_dir.mkdir()

        try:
            outcome = await drive_engine(
                _DOT_SOURCE,
                coordinator,
                cwd=Path(tmpdir),
                logs_root=logs_dir,
                transform=True,
            )
        except Exception as exc:
            print(
                f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
                f"drive_engine raised unexpectedly on standalone transport path: {exc}"
            )
            sys.exit(1)

        # Primary assertion: the per-node status.json must record the runtime-random
        # preferred_next_label and is_explicit=true. This is the exact observable
        # symptom the issue reporter described.
        status_path = logs_dir / _BOX_NODE_ID / "status.json"
        if not status_path.exists():
            print(
                f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
                f"per-node status.json not written at {status_path} "
                f"(logs_dir contents: {list(logs_dir.iterdir())})"
            )
            sys.exit(1)

        try:
            status_data = json.loads(status_path.read_text())
        except Exception as exc:
            print(
                f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
                f"could not parse status.json at {status_path}: {exc}"
            )
            sys.exit(1)

        actual_label = status_data.get("preferred_next_label")
        actual_explicit = status_data.get("is_explicit")

        if actual_label != EXPECTED_LABEL:
            print(
                f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
                f"status.json: expected preferred_next_label={EXPECTED_LABEL!r}, "
                f"got {actual_label!r} (full status: {status_data})"
            )
            sys.exit(1)

        if actual_explicit is not True:
            print(
                f"DEFECT: report_outcome metadata ignored when output is non-empty -- "
                f"status.json: expected is_explicit=true, "
                f"got {actual_explicit!r} (full status: {status_data})"
            )
            sys.exit(1)

        print(
            f"Probe2 PASSED: status.json has preferred_next_label={actual_label!r}, "
            f"is_explicit={actual_explicit}"
        )


asyncio.run(_run_probe())
print("Probe 2 PASSED: drive_engine transport round-trip -- defect is not present")
sys.exit(0)
PYEOF2

# ---------------------------------------------------------------------------
# Run Probe 1: standalone Python -- NOT via pytest.
# The shell observes the exit code and stdout directly.
# No pytest conftest.py hook can intercept this execution.
# ---------------------------------------------------------------------------

echo "--- Running Probe 1: backend standalone round-trip (loop-pipeline) ---"
PROBE1_RC=0
set +e
(
    cd "$REPO_ROOT"
    PYTHONPATH="$LOOP_SRC_DIR" uv run --project "$LOOP_MODULE_DIR" python3 "$PROBE1_PY"
) 2>&1 | tee "$PROBE1_OUT"
PROBE1_RC=${PIPESTATUS[0]}
set -e

if [ "$PROBE1_RC" -eq 1 ]; then
    echo "DEFECT: report_outcome metadata ignored when output is non-empty"
    exit 1
elif [ "$PROBE1_RC" -ne 0 ]; then
    echo "INFRA: Probe 1 exited with rc=${PROBE1_RC} (not a test failure -- infrastructure problem)"
    exit 2
fi

# Verify the probe actually ran its assertions (not a vacuous exit 0).
if ! grep -q "Probe 1 PASSED" "$PROBE1_OUT"; then
    echo "DEFECT: report_outcome metadata ignored when output is non-empty"
    echo "Probe 1 did not produce expected completion message -- assertions may not have run"
    cat "$PROBE1_OUT"
    exit 1
fi

echo "Gate [loop-pipeline]: Probe 1 PASSED -- defect is not present"

# ---------------------------------------------------------------------------
# Run Probe 2: standalone Python -- NOT via pytest.
# The shell observes the exit code and stdout directly.
# No pytest conftest.py hook can intercept this execution.
# ---------------------------------------------------------------------------

echo "--- Running Probe 2: drive_engine transport round-trip (pipeline-runner) ---"
PROBE2_RC=0
set +e
(
    cd "$REPO_ROOT"
    PYTHONPATH="$LOOP_SRC_DIR:$RUNNER_SRC_DIR" uv run --project "$RUNNER_MODULE_DIR" python3 "$PROBE2_PY"
) 2>&1 | tee "$PROBE2_OUT"
PROBE2_RC=${PIPESTATUS[0]}
set -e

if [ "$PROBE2_RC" -eq 1 ]; then
    echo "DEFECT: report_outcome metadata ignored when output is non-empty"
    exit 1
elif [ "$PROBE2_RC" -ne 0 ]; then
    echo "INFRA: Probe 2 exited with rc=${PROBE2_RC} (not a test failure -- infrastructure problem)"
    exit 2
fi

# Verify the probe actually ran its assertions (not a vacuous exit 0).
if ! grep -q "Probe 2 PASSED" "$PROBE2_OUT"; then
    echo "DEFECT: report_outcome metadata ignored when output is non-empty"
    echo "Probe 2 did not produce expected completion message -- assertions may not have run"
    cat "$PROBE2_OUT"
    exit 1
fi

echo "Gate [pipeline-runner]: Probe 2 PASSED -- defect is not present"

# ---------------------------------------------------------------------------
# Also fold in the existing test that covers the adjacent empty-output path,
# to confirm the fix does not regress the already-working case.
# This runs via pytest but is NOT the primary round-trip assertion -- the
# positive runtime-random label round-trips above (standalone scripts) are.
# ---------------------------------------------------------------------------
echo "--- Running existing backend test (empty-output path regression guard) ---"
EXISTING_RC=0
set +e
(
    cd "$REPO_ROOT"
    uv run --project "$LOOP_MODULE_DIR" pytest \
        "$LOOP_MODULE_DIR/tests/test_backend.py::test_spawn_empty_output_with_report_outcome_does_not_fall_back" \
        -v 2>&1
) | tee "$PROBE_DIR/existing.out"
EXISTING_RC=${PIPESTATUS[0]}
set -e

if [ "$EXISTING_RC" -eq 1 ]; then
    echo "DEFECT: report_outcome metadata ignored when output is non-empty"
    echo "(existing empty-output test regressed)"
    exit 1
elif [ "$EXISTING_RC" -ne 0 ]; then
    echo "INFRA: existing test runner exited with rc=${EXISTING_RC}"
    exit 2
fi

echo "Gate: all probes PASSED -- defect is not present"
exit 0
