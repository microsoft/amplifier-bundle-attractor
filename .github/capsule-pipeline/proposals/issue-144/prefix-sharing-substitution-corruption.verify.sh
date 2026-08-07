#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Verify: $key substitution must not corrupt undefined variables whose names
# share a prefix with a defined variable.
#
# Exit codes:
#   0  — defect is NOT present (all assertions pass)
#   1  — defect IS present (at least one assertion genuinely failed;
#        red_signal printed)
#   2+ — infrastructure problem (missing tool, missing module, uncaught
#        exception in the assertion harness itself, unexpected test
#        failure, etc.) — NOT the same thing as the defect being present.
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

# Assertion 5: expand_params — functionality/regression (mirrors assertion 4).
# Assertion 3 only forbids the *corrupted* output; it never proves that
# real substitution happens at all, so a no-op stub (return text unchanged)
# would satisfy assertion 3 while silently deleting the feature. This
# assertion closes that hole: expand_params MUST actually substitute a
# known, unambiguous (non-prefix-colliding) param.
r5 = expand_params("Build a $framework app", {"framework": "FastAPI"})
if r5 != "Build a FastAPI app":
    failures.append(f"FAIL expand_params functionality (no-op stub would pass assertion 3 alone): got {r5!r}, want 'Build a FastAPI app'")

if failures:
    for f in failures:
        print(f)
    # Distinct exit code (3) for "assertions ran and genuinely failed", as
    # opposed to any other non-zero exit (uncaught exception, import error,
    # etc.) which must be treated as an infrastructure problem, not a
    # confirmed-red result. See the ASSERTION_RC handling below.
    sys.exit(3)

sys.exit(0)
EOF
)" || ASSERTION_RC=$?

if [ $ASSERTION_RC -eq 3 ]; then
    # At least one assertion genuinely failed (the harness ran to completion
    # and its own failure path chose to exit(3)) — print red_signal and exit 1.
    echo "$RESULT"
    echo "SUFFIXED=Alice_suffix"
    exit 1
elif [ $ASSERTION_RC -ne 0 ]; then
    # Any other non-zero code (e.g. an uncaught exception/import error inside
    # the heredoc, which Python reports as exit 1) is NOT the same thing as
    # the defect being confirmed present — it means the harness itself broke.
    # Treat it as an infrastructure problem so it can never be misread as
    # "RED for the right reason".
    echo "INFRA: uv run exited with unexpected code $ASSERTION_RC (harness error, not a confirmed assertion failure)" >&2
    echo "$RESULT" >&2
    exit 2
fi

# --- existing unit tests must still pass ------------------------------------
#
# Fold in every existing test file that already exercises this code path.
# test_param_expansion.py in particular independently catches a no-op
# expand_params() stub (6/9 of its assertions fail against such a stub),
# and test_transforms.py exercises expand_variables()/expand_goal_variable()
# on the same execution path. Both are cheap (already-passing, no new
# dependencies) and close the gate hole independently of assertion 5 above.

TEST_FILES=(
    "tests/test_unified_substitution.py"
    "tests/test_param_expansion.py"
    "tests/test_transforms.py"
)

for TEST_FILE in "${TEST_FILES[@]}"; do
    if [ ! -f "$TEST_FILE" ]; then
        echo "INFRA: expected test file not found: $MODULE_DIR/$TEST_FILE" >&2
        exit 2
    fi
done

if ! uv run pytest "${TEST_FILES[@]}" -q --tb=short 2>&1; then
    echo "INFRA: existing substitution/param/transform unit tests failed unexpectedly" >&2
    exit 2
fi

# All assertions passed — defect is not present
exit 0
