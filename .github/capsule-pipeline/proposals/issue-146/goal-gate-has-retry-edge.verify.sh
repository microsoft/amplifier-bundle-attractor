set -euo pipefail

# ---------------------------------------------------------------------------
# DEFINITION.verify.sh — gate for goal_gate_has_retry false-positive on
# 00-convergence-loop.dot.
#
# Exit codes:
#   0  defect NOT present (lint() returns no goal_gate_has_retry diagnostic
#      for test_gate — either already fixed or was never present)
#   1  defect IS present (the WARNING fires — assertion failure)
#   2+ infrastructure problem (missing binary, missing file, etc.)
# ---------------------------------------------------------------------------

MODULE_DIR="modules/loop-pipeline"
EXAMPLE_FILE="examples/pipelines/00-convergence-loop.dot"

# --- Infrastructure guards -------------------------------------------------

if ! command -v uv >/dev/null 2>&1; then
    echo "INFRA ERROR: 'uv' not found in PATH" >&2
    exit 2
fi

if [ ! -d "$MODULE_DIR" ]; then
    echo "INFRA ERROR: module directory '$MODULE_DIR' not found (cwd: $(pwd))" >&2
    exit 2
fi

if [ ! -f "$EXAMPLE_FILE" ]; then
    echo "INFRA ERROR: example file '$EXAMPLE_FILE' not found (cwd: $(pwd))" >&2
    exit 2
fi

if [ ! -f "$MODULE_DIR/pyproject.toml" ]; then
    echo "INFRA ERROR: '$MODULE_DIR/pyproject.toml' not found" >&2
    exit 2
fi

# --- Check the module is importable ----------------------------------------

cd "$MODULE_DIR" || { echo "INFRA ERROR: cannot cd to '$MODULE_DIR'" >&2; exit 2; }

if ! uv run python -c "from amplifier_module_loop_pipeline.validation import lint; from amplifier_module_loop_pipeline.dot_parser import parse_dot" 2>/dev/null; then
    echo "INFRA ERROR: amplifier_module_loop_pipeline is not importable" >&2
    exit 2
fi

# --- Run the assertion -------------------------------------------------------
# Parse the example and run lint(); check whether any diagnostic has
# rule="goal_gate_has_retry" for node_id="test_gate".

RESULT=$(uv run python - <<'PYEOF'
import sys
import pathlib

# Resolve the example path relative to the repo root (two levels up from
# modules/loop-pipeline).
repo_root = pathlib.Path(__file__).resolve().parents[2] if hasattr(pathlib.Path(__file__), 'parents') else pathlib.Path.cwd().parents[1]

# __file__ is not set in -c / heredoc mode; use cwd-relative path instead.
example_path = pathlib.Path("../../examples/pipelines/00-convergence-loop.dot").resolve()

if not example_path.exists():
    print("INFRA:missing_example", flush=True)
    sys.exit(0)  # let the outer shell guard handle it

from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.validation import lint

dot_text = example_path.read_text(encoding="utf-8")
graph = parse_dot(dot_text)
diags = lint(graph)

hits = [
    d for d in diags
    if d.rule == "goal_gate_has_retry" and d.node_id == "test_gate"
]

if hits:
    print("DEFECT_PRESENT", flush=True)
else:
    print("DEFECT_ABSENT", flush=True)
PYEOF
)

case "$RESULT" in
    DEFECT_PRESENT)
        echo "ASSERTION FAILED: goal_gate_has_retry WARNING still fires for test_gate in 00-convergence-loop.dot" >&2
        echo "  The lint rule emits a false positive: test_gate has goal_gate=true and a loop_restart back-edge" >&2
        echo "  but lint() still reports: goal_gate_has_retry" >&2
        exit 1
        ;;
    DEFECT_ABSENT)
        echo "OK: no goal_gate_has_retry diagnostic for test_gate — defect not present"
        exit 0
        ;;
    INFRA:missing_example)
        echo "INFRA ERROR: example file could not be resolved from module directory" >&2
        exit 2
        ;;
    *)
        echo "INFRA ERROR: unexpected output from lint check: '$RESULT'" >&2
        exit 2
        ;;
esac
