set -euo pipefail

# ---------------------------------------------------------------------------
# Verify: $key substitution must not corrupt undefined variables whose names
# share a prefix with a defined variable.
#
# Exit codes:
#   0  — defect is NOT present (all assertions pass)
#   1  — defect IS present (at least one assertion failed; red_signal printed)
#   2+ — infrastructure problem (missing tool, missing module, etc.)
# ---------------------------------------------------------------------------

REPO_ROOT="$(pwd)"
MODULE_DIR="$REPO_ROOT/modules/loop-pipeline"

# --- infrastructure guards --------------------------------------------------

if ! command -v uv >/dev/null 2>&1; then
    echo "INFRA: 'uv' not found on PATH" >&2
    exit 2
fi

if [ ! -d "$MODULE_DIR" ]; then
    echo "INFRA: expected module directory not found: $MODULE_DIR" >&2
    exit 2
fi

if [ ! -f "$MODULE_DIR/amplifier_module_loop_pipeline/substitution.py" ]; then
    echo "INFRA: substitution.py not found under $MODULE_DIR" >&2
    exit 2
fi

if [ ! -f "$MODULE_DIR/amplifier_module_loop_pipeline/transforms.py" ]; then
    echo "INFRA: transforms.py not found under $MODULE_DIR" >&2
    exit 2
fi

# --- run assertions via uv (uses the module's own isolated environment) ------

cd "$MODULE_DIR" || { echo "INFRA: cannot cd to $MODULE_DIR" >&2; exit 2; }

ASSERTION_RC=0
RESULT="$(uv run python3 - <<'EOF' 2>&1
import sys
from amplifier_module_loop_pipeline.substitution import substitute_context
from amplifier_module_loop_pipeline.transforms import expand_params

failures = []

# Assertion 1: substitute_context — name/name_suffix prefix collision
r1 = substitute_context("echo NAME=$name SUFFIXED=$name_suffix", {"name": "Alice"})
if "Alice_suffix" in r1:
    failures.append(f"FAIL substitute_context name/name_suffix: got {r1!r}, want 'echo NAME=Alice SUFFIXED=$name_suffix'")

# Assertion 2: substitute_context — id/id2 variant
r2 = substitute_context("echo ID=$id ID2=$id2", {"id": "42"})
if "422" in r2:
    failures.append(f"FAIL substitute_context id/id2: got {r2!r}, want 'echo ID=42 ID2=$id2'")

# Assertion 3: expand_params — same boundary requirement
r3 = expand_params("echo NAME=$name SUFFIXED=$name_suffix", {"name": "Alice"})
if "Alice_suffix" in r3:
    failures.append(f"FAIL expand_params name/name_suffix: got {r3!r}, want 'echo NAME=Alice SUFFIXED=$name_suffix'")

# Assertion 4: regression — defined-both case must still work
r4 = substitute_context("$tool.output and $tool", {"tool": "base", "tool.output": "dotted_value"})
if "dotted_value" not in r4 or "base" not in r4:
    failures.append(f"FAIL substitute_context longest-key-wins regression: got {r4!r}")

if failures:
    for f in failures:
        print(f)
    sys.exit(1)

sys.exit(0)
EOF
)" || ASSERTION_RC=$?

if [ $ASSERTION_RC -eq 1 ]; then
    # At least one assertion failed — print red_signal and exit 1
    echo "$RESULT"
    echo "SUFFIXED=Alice_suffix"
    exit 1
elif [ $ASSERTION_RC -ne 0 ]; then
    # Unexpected error from the Python invocation itself
    echo "INFRA: uv run exited with unexpected code $ASSERTION_RC" >&2
    echo "$RESULT" >&2
    exit 2
fi

# --- existing unit tests must still pass ------------------------------------

TEST_FILE="tests/test_unified_substitution.py"
if [ ! -f "$TEST_FILE" ]; then
    echo "INFRA: expected test file not found: $MODULE_DIR/$TEST_FILE" >&2
    exit 2
fi

if ! uv run pytest "$TEST_FILE" -q --tb=short 2>&1; then
    echo "INFRA: existing substitution unit tests failed unexpectedly" >&2
    exit 2
fi

# All assertions passed — defect is not present
exit 0
