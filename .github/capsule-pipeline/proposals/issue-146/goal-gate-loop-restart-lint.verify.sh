set -euo pipefail

# Verify that lint() no longer fires a spurious goal_gate_has_retry WARNING
# on examples/pipelines/00-convergence-loop.dot.
#
# Red  (exit 1): the diagnostic "Node 'test_gate' has goal_gate=true but no retry_target"
#                is present in the lint output — defect reproduces.
# Green (exit 0): the diagnostic is absent — defect is fixed.
# Infrastructure error (exit 2): prerequisite missing or environment broken.

TARGET_DIR="$(pwd)"

# --- Prerequisites ---

EXAMPLE_DOT="$TARGET_DIR/examples/pipelines/00-convergence-loop.dot"
if [ ! -f "$EXAMPLE_DOT" ]; then
    echo "INFRA ERROR: example file not found: $EXAMPLE_DOT" >&2
    exit 2
fi

MODULE_DIR="$TARGET_DIR/modules/loop-pipeline"
if [ ! -d "$MODULE_DIR" ]; then
    echo "INFRA ERROR: module directory not found: $MODULE_DIR" >&2
    exit 2
fi

# Locate a Python interpreter with the module available.
# Prefer the module's own venv if present; fall back to whatever python3 is on PATH.
VENV_PYTHON="$MODULE_DIR/.venv/bin/python"
if [ -x "$VENV_PYTHON" ]; then
    PYTHON="$VENV_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
else
    echo "INFRA ERROR: no python3 found on PATH and no venv at $VENV_PYTHON" >&2
    exit 2
fi

# Verify the module is importable.
if ! "$PYTHON" -c "import amplifier_module_loop_pipeline" 2>/dev/null; then
    # Try installing via uv sync first.
    if command -v uv >/dev/null 2>&1; then
        (cd "$MODULE_DIR" && uv sync --quiet 2>/dev/null) || true
        PYTHON="$VENV_PYTHON"
    fi
    if ! "$PYTHON" -c "import amplifier_module_loop_pipeline" 2>/dev/null; then
        echo "INFRA ERROR: amplifier_module_loop_pipeline is not importable" >&2
        exit 2
    fi
fi

# Locate pytest.
PYTEST_BIN="$(dirname "$PYTHON")/pytest"
if [ ! -x "$PYTEST_BIN" ]; then
    if command -v pytest >/dev/null 2>&1; then
        PYTEST_BIN="pytest"
    else
        echo "INFRA ERROR: pytest not found (checked $PYTEST_BIN and PATH)" >&2
        exit 2
    fi
fi

TEST_VALIDATION="$MODULE_DIR/tests/test_validation.py"
if [ ! -f "$TEST_VALIDATION" ]; then
    echo "INFRA ERROR: test file not found: $TEST_VALIDATION" >&2
    exit 2
fi

TEST_EXAMPLES_LINT="$MODULE_DIR/tests/test_examples_lint_clean.py"
if [ ! -f "$TEST_EXAMPLES_LINT" ]; then
    echo "INFRA ERROR: test file not found: $TEST_EXAMPLES_LINT" >&2
    exit 2
fi

# --- Assertion 1: lint() on 00-convergence-loop.dot must not emit the spurious warning ---

RED_SIGNAL="Node 'test_gate' has goal_gate=true but no retry_target"

LINT_OUTPUT="$("$PYTHON" -c "
import sys
from pathlib import Path

try:
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.validation import lint
except ImportError as e:
    print('IMPORT ERROR: ' + str(e), file=sys.stderr)
    sys.exit(2)

dot_path = Path(sys.argv[1])
dot_text = dot_path.read_text(encoding='utf-8')
graph = parse_dot(dot_text)
diags = lint(graph)
for d in diags:
    print(d.rule + ' ' + d.severity + ' ' + d.message)
" "$EXAMPLE_DOT")"

if echo "$LINT_OUTPUT" | grep -qF "$RED_SIGNAL"; then
    echo "FAIL: $RED_SIGNAL"
    exit 1
fi

echo "OK: lint() on 00-convergence-loop.dot does not emit the spurious goal_gate_has_retry warning"

# --- Assertion 2: test_validation.py must pass (covers goal_gate_has_retry rule) ---

echo "Running test_validation.py ..."
if ! (cd "$MODULE_DIR" && "$PYTEST_BIN" tests/test_validation.py -q 2>&1); then
    echo "FAIL: tests/test_validation.py did not pass" >&2
    exit 1
fi

# --- Assertion 3: test_examples_lint_clean.py must pass (no ERROR regressions) ---

echo "Running test_examples_lint_clean.py ..."
if ! (cd "$MODULE_DIR" && "$PYTEST_BIN" tests/test_examples_lint_clean.py -q 2>&1); then
    echo "FAIL: tests/test_examples_lint_clean.py did not pass" >&2
    exit 1
fi

echo "All checks passed."
exit 0
