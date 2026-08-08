set -euo pipefail

# ---------------------------------------------------------------------------
# Infrastructure guards
# ---------------------------------------------------------------------------

# Require the loop-pipeline module directory.
MODULE_DIR="modules/loop-pipeline"
if [ ! -d "$MODULE_DIR" ]; then
    echo "INFRA ERROR: expected directory '$MODULE_DIR' not found (wrong cwd?)" >&2
    exit 2
fi

# Require the DOT file under test.
DOT_FILE="examples/pipelines/practical/bug-fix.dot"
if [ ! -f "$DOT_FILE" ]; then
    echo "INFRA ERROR: '$DOT_FILE' not found" >&2
    exit 2
fi

# Require uv.
if ! command -v uv >/dev/null 2>&1; then
    echo "INFRA ERROR: 'uv' not found on PATH" >&2
    exit 2
fi

# Require the existing test files.
if [ ! -f "$MODULE_DIR/tests/test_examples_lint_clean.py" ]; then
    echo "INFRA ERROR: '$MODULE_DIR/tests/test_examples_lint_clean.py' not found" >&2
    exit 2
fi
if [ ! -f "$MODULE_DIR/tests/test_command_content_lint.py" ]; then
    echo "INFRA ERROR: '$MODULE_DIR/tests/test_command_content_lint.py' not found" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Step 1: run the existing test files that exercise lint() and the CMD-001
# rule specifically.  These must stay green regardless of whether the defect
# is present or absent.  A non-zero exit here is an infrastructure problem,
# not a defect assertion.
# ---------------------------------------------------------------------------

cd "$MODULE_DIR"
pytest_rc=0
uv run pytest \
    tests/test_examples_lint_clean.py \
    tests/test_command_content_lint.py \
    -q 2>&1 || pytest_rc=$?
cd ../..

if [ "$pytest_rc" -ne 0 ]; then
    echo "INFRA ERROR: existing tests failed (exit $pytest_rc) -- not a defect assertion" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Step 2: assert the specific defect -- CMD-001 on verdict_gate in bug-fix.dot.
# Exit 1 (with the exact red_signal substring) if the defect is present.
# Exit 0 if the defect is absent (already fixed).
# ---------------------------------------------------------------------------

LINT_OUTPUT=$(
    cd "$MODULE_DIR"
    uv run python - <<'PYEOF'
import sys
from pathlib import Path
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.validation import lint

# cwd is modules/loop-pipeline; the repo root is two levels up.
dot_path = Path("../../examples/pipelines/practical/bug-fix.dot")
graph = parse_dot(dot_path.read_text(encoding="utf-8"))
diags = lint(graph)
hits = [d for d in diags if d.rule == "CMD-001" and d.node_id == "verdict_gate"]
for d in hits:
    print(f"[{d.severity}] [{d.rule}] [{d.node_id}] {d.message}")
sys.exit(0)
PYEOF
)

if echo "$LINT_OUTPUT" | grep -qF "[CMD-001] [verdict_gate] Tool node 'verdict_gate' tool_command ends in a pipe to 'grep' without pipefail"; then
    echo "$LINT_OUTPUT"
    echo "[CMD-001] [verdict_gate] Tool node 'verdict_gate' tool_command ends in a pipe to 'grep' without pipefail"
    exit 1
fi

echo "OK: no CMD-001 findings on verdict_gate in $DOT_FILE"
exit 0
