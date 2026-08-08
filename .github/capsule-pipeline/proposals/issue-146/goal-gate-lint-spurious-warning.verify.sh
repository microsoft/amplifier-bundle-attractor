set -euo pipefail

# --- prerequisite guards ---

if ! command -v uv >/dev/null 2>&1; then
    echo "INFRA ERROR: uv not found on PATH" >&2
    exit 2
fi

MODULE_DIR="modules/loop-pipeline"
if [ ! -d "$MODULE_DIR" ]; then
    echo "INFRA ERROR: expected directory $MODULE_DIR not found (cwd: $PWD)" >&2
    exit 2
fi

EXAMPLE_FILE="examples/pipelines/00-convergence-loop.dot"
if [ ! -f "$EXAMPLE_FILE" ]; then
    echo "INFRA ERROR: expected file $EXAMPLE_FILE not found (cwd: $PWD)" >&2
    exit 2
fi

# --- run the existing lint-rule test suite and examples corpus sweep ---
# These cover _check_goal_gate_has_retry and lint() in the module source.

cd "$MODULE_DIR" || { echo "INFRA ERROR: cd $MODULE_DIR failed" >&2; exit 2; }

if ! uv run pytest \
        tests/test_validation.py \
        tests/test_examples_lint_clean.py \
        -q --no-header 2>&1; then
    echo "INFRA ERROR: existing test suite failed unexpectedly" >&2
    exit 2
fi

cd ../..

# --- targeted assertion: the defect ---
# lint() on 00-convergence-loop.dot must emit NO goal_gate_has_retry diagnostic.
# If it does emit one, print the red_signal substring and exit 1.

LINT_OUTPUT=$(cd modules/loop-pipeline && uv run python - <<'PYEOF'
import sys
from pathlib import Path
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.validation import lint

example = Path("../../examples/pipelines/00-convergence-loop.dot").read_text(encoding="utf-8")
graph = parse_dot(example)
diags = lint(graph)

hits = [
    d for d in diags
    if d.rule == "goal_gate_has_retry" and d.node_id == "test_gate"
]
for d in hits:
    print(d.message)
sys.exit(0)
PYEOF
)

if [ -n "$LINT_OUTPUT" ]; then
    echo "FAIL: lint() on 00-convergence-loop.dot emits goal_gate_has_retry WARNING:"
    echo "$LINT_OUTPUT"
    # Print the exact red_signal substring so the gate is identifiable:
    echo "Node 'test_gate' has goal_gate=true but no retry_target"
    exit 1
fi

echo "OK: lint() on 00-convergence-loop.dot emits no goal_gate_has_retry diagnostic."
exit 0
