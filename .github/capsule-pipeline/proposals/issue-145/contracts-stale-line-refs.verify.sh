set -euo pipefail

# Verify that docs/CONTRACTS.md line references for engine.py and
# edge_selection.py are accurate (i.e., the described code lives at the
# claimed lines).
#
# Exit codes:
#   0  -- defect NOT present (references are accurate, or the stale numbers
#          are gone and replaced with something accurate)
#   1  -- defect IS present (at least one claimed line does not contain the
#          described code)
#   2  -- infrastructure problem (missing file, missing tool, etc.)

ENGINE="modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py"
EDGE="modules/loop-pipeline/amplifier_module_loop_pipeline/edge_selection.py"
CONTRACTS="docs/CONTRACTS.md"

# --- prerequisite guards ---------------------------------------------------

for cmd in sed grep; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "INFRA: required command '$cmd' not found" >&2
        exit 2
    }
done

for f in "$ENGINE" "$EDGE" "$CONTRACTS"; do
    [ -f "$f" ] || {
        echo "INFRA: required file '$f' not found" >&2
        exit 2
    }
done

failures=0

# ---------------------------------------------------------------------------
# Check 1: CONTRACTS.md Section 2 reference block claims engine.py lines
# 597-600 hold the `continue_on_fail` check.
#
# Strategy: if the doc still contains a line-number reference for the
# `continue_on_fail` check in engine.py (Section 2 reference block), extract
# that number and verify `continue_on_fail` appears in the four-line window
# starting there.  If the stale number is gone (doc has been corrected or
# switched to a symbol anchor), the check passes trivially.
#
# The Section 2 reference block is the line that mentions both "continue_on_fail"
# and "_get_runs_on" together with line numbers -- it is the "Reference:" line
# at the bottom of the Fail-Fast Policy section.
# ---------------------------------------------------------------------------

# Extract the line number claimed for the continue_on_fail check from the
# Section 2 reference line (which mentions _get_runs_on on the same or next line).
cof_claimed=$(grep -A1 "continue_on_fail.*check\|check.*continue_on_fail" "$CONTRACTS" 2>/dev/null \
    | grep -oE '\(lines [0-9]+' | grep -oE '[0-9]+' | head -1 || true)

# Fallback: look for the Section 6 table row for continue_on_fail override
if [ -z "$cof_claimed" ]; then
    cof_claimed=$(grep "continue_on_fail.*override\|override.*continue_on_fail" "$CONTRACTS" 2>/dev/null \
        | grep -oE 'Lines [0-9]+' | grep -oE '[0-9]+' | head -1 || true)
fi

if [ -n "$cof_claimed" ]; then
    # A bare line number is still claimed; verify it points at continue_on_fail.
    window=$(sed -n "${cof_claimed},$((cof_claimed + 3))p" "$ENGINE" 2>/dev/null || true)
    if ! echo "$window" | grep -qF "continue_on_fail"; then
        actual=$(sed -n "${cof_claimed}p" "$ENGINE" 2>/dev/null || echo "(could not read)")
        echo "FAIL: engine.py lines ${cof_claimed}-$((cof_claimed + 3)) do not contain 'continue_on_fail'"
        echo "      (found: '$(echo "$actual" | sed 's/^[[:space:]]*//')')"
        failures=$((failures + 1))
    fi
fi

# ---------------------------------------------------------------------------
# Check 2: CONTRACTS.md claims the outcome-status guard in edge_selection.py
# is at a specific line number ("outcome-status guard at line N").
#
# If that claim is still present, verify the claimed line contains
# `outcome.status != StageStatus.FAIL`.  If the claim is gone (doc corrected),
# the check passes trivially.
# ---------------------------------------------------------------------------

guard_claimed=$(grep -m1 "outcome-status guard at line" "$CONTRACTS" 2>/dev/null \
    | grep -oE 'line [0-9]+' | grep -oE '[0-9]+' | head -1 || true)

if [ -n "$guard_claimed" ]; then
    guard_line=$(sed -n "${guard_claimed}p" "$EDGE" 2>/dev/null || true)
    if ! echo "$guard_line" | grep -qF "outcome.status != StageStatus.FAIL"; then
        echo "FAIL: edge_selection.py line ${guard_claimed} does not contain 'outcome.status != StageStatus.FAIL'"
        echo "      (found: '$(echo "$guard_line" | sed 's/^[[:space:]]*//')')"
        failures=$((failures + 1))
    fi
fi

# ---------------------------------------------------------------------------
# Check 3: CONTRACTS.md claims a specific line number for `_get_runs_on` in
# engine.py.  Verify that claimed line is actually inside the `_get_runs_on`
# function body (i.e., between the def line for _get_runs_on and the def line
# for _check_node_skip).
#
# If the claim is gone (doc corrected to a symbol anchor or accurate number),
# the check passes trivially.
# ---------------------------------------------------------------------------

runs_on_claimed=$(grep -m1 "_get_runs_on" "$CONTRACTS" 2>/dev/null \
    | grep -oE '[Ll]ines? [0-9]+' | grep -oE '[0-9]+' | tail -1 || true)

if [ -n "$runs_on_claimed" ]; then
    actual_get_runs_on=$(grep -n "def _get_runs_on" "$ENGINE" 2>/dev/null \
        | head -1 | cut -d: -f1 || true)
    actual_check_skip=$(grep -n "def _check_node_skip" "$ENGINE" 2>/dev/null \
        | head -1 | cut -d: -f1 || true)

    if [ -n "$actual_get_runs_on" ] && [ -n "$actual_check_skip" ]; then
        if [ "$runs_on_claimed" -lt "$actual_get_runs_on" ] || \
           [ "$runs_on_claimed" -ge "$actual_check_skip" ]; then
            actual_content=$(sed -n "${runs_on_claimed}p" "$ENGINE" 2>/dev/null || echo "(could not read)")
            echo "FAIL: engine.py line ${runs_on_claimed} is not inside '_get_runs_on'"
            echo "      (found: '$(echo "$actual_content" | sed 's/^[[:space:]]*//')')"
            echo "      (_get_runs_on is at line ${actual_get_runs_on}; _check_node_skip is at line ${actual_check_skip})"
            failures=$((failures + 1))
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

if [ "$failures" -gt 0 ]; then
    exit 1
fi

exit 0
