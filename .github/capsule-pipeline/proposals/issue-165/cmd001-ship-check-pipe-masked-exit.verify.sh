set -euo pipefail

# ---------------------------------------------------------------------------
# Verify: examples/patterns/task-runner.dot ship_check node is CMD-001-clean
#
# RED  (exit 1): lint() returns a CMD-001 diagnostic for the ship_check node
# GREEN (exit 0): lint() returns zero CMD-001 diagnostics for ship_check
# INFRA (exit 2): missing prerequisite — not a defect assertion failure
# ---------------------------------------------------------------------------

# --- infrastructure guards -------------------------------------------------

REPO_ROOT="$(pwd)"

# Confirm we are in the right repo root
if [ ! -f "examples/patterns/task-runner.dot" ]; then
    echo "INFRA: examples/patterns/task-runner.dot not found in $(pwd)" >&2
    exit 2
fi

MODULE_DIR="modules/loop-pipeline"
if [ ! -d "$MODULE_DIR" ]; then
    echo "INFRA: $MODULE_DIR not present in $(pwd)" >&2
    exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "INFRA: python3 not found on PATH" >&2
    exit 2
fi

# Confirm the module is importable (uv sync may be needed in CI; here we just
# check that the package is importable and emit a clear message if not)
if ! python3 -c "import amplifier_module_loop_pipeline" 2>/dev/null; then
    echo "INFRA: amplifier_module_loop_pipeline not importable; run 'uv sync' in $MODULE_DIR" >&2
    exit 2
fi

# --- run the existing test suites that cover lint / CMD-001 ----------------
# test_examples_lint_clean.py  — sweeps the examples corpus through lint()
# test_command_content_lint.py — unit tests for CMD-001 and CMD-002 rules
#
# These are the repo's own tests for the lint() function and CMD-001 rule.
# Running them first catches regressions in the lint machinery itself.

cd "$MODULE_DIR" || { echo "INFRA: could not cd into $MODULE_DIR" >&2; exit 2; }

python3 -m pytest tests/test_examples_lint_clean.py tests/test_command_content_lint.py -q 2>&1

cd "$REPO_ROOT" || { echo "INFRA: could not cd back to repo root" >&2; exit 2; }

# --- primary assertion: zero CMD-001 findings on ship_check ----------------
# This is the end-state behavior described in the issue: lint(task-runner.dot)
# must return no CMD-001 diagnostics for the ship_check node.

set +e
LINT_OUTPUT="$(python3 - <<'PYEOF'
import sys
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.validation import lint

dot_text = open("examples/patterns/task-runner.dot", encoding="utf-8").read()
graph = parse_dot(dot_text)
diags = lint(graph)

cmd001_ship = [d for d in diags if d.rule == "CMD-001" and d.node_id == "ship_check"]

if cmd001_ship:
    for d in cmd001_ship:
        print(f"[{d.severity}] [{d.rule}] [{d.node_id}] {d.message}")
    sys.exit(1)
else:
    sys.exit(0)
PYEOF
)"
LINT_RC=$?
set -e

if [ "$LINT_RC" -eq 1 ]; then
    echo "$LINT_OUTPUT"
    echo "CMD-001 ship_check pipe-masked exit detected"
    exit 1
elif [ "$LINT_RC" -ne 0 ]; then
    echo "INFRA: lint check script exited with unexpected code $LINT_RC" >&2
    exit 2
fi

echo "OK: lint(task-runner.dot) returns zero CMD-001 findings for ship_check"
exit 0
