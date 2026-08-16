set -euo pipefail

# ---------------------------------------------------------------------------
# Gate: TOPO-006 false negative — conditional-plus-plain-edge receiver
# silently passes failure to the exit node.
#
# Exit codes:
#   0  — defect is NOT present (already fixed, or gate does not capture it)
#   1  — defect IS present (assertion failure; red_signal printed)
#  >=2 — infrastructure problem (missing tooling, repo layout mismatch)
# ---------------------------------------------------------------------------

REPO_ROOT="$(pwd)"
MODULE_DIR="$REPO_ROOT/modules/loop-pipeline"

# -- Infrastructure guards ---------------------------------------------------

if ! command -v uv >/dev/null 2>&1; then
    echo "INFRA: uv not found on PATH" >&2
    exit 2
fi

if [ ! -d "$MODULE_DIR" ]; then
    echo "INFRA: expected module directory not found: $MODULE_DIR" >&2
    exit 2
fi

if [ ! -f "$MODULE_DIR/pyproject.toml" ]; then
    echo "INFRA: pyproject.toml not found in $MODULE_DIR" >&2
    exit 2
fi

# Generate semantically neutral runtime names for probe nodes so they cannot
# be enumerated by a name-specific dodge.  Names are random alphanumeric
# sequences with no domain vocabulary from the issue's subject space.
SUFFIX_A="${RANDOM}${RANDOM}"
SUFFIX_B="${RANDOM}${RANDOM}"

NODE_ENTRY="nd${SUFFIX_A}e"
NODE_ALPHA="nd${SUFFIX_A}a"
NODE_BETA="nd${SUFFIX_A}b"
NODE_GAMMA="nd${SUFFIX_A}g"
NODE_EXIT="nd${SUFFIX_A}x"

NODE_RG_ENTRY="nd${SUFFIX_B}e"
NODE_RG_WORK="nd${SUFFIX_B}w"
NODE_RG_CHECK="nd${SUFFIX_B}c"
NODE_RG_GATE="nd${SUFFIX_B}g"
NODE_RG_BACK="nd${SUFFIX_B}k"
NODE_RG_ALT="nd${SUFFIX_B}l"
NODE_RG_EXIT="nd${SUFFIX_B}x"

# Use a temp file to capture probe output without triggering set -e on rc=1.
PROBE_TMP="$(mktemp)"
trap 'rm -f "$PROBE_TMP"' EXIT

# ---------------------------------------------------------------------------
# Probe 1 (behavioral): hazard graph — conditional + plain edge to exit.
# lint() must return at least one fail_routed_to_exit diagnostic.
# Probe 2 (mixed-case): true re-gate — all outgoing edges conditional, no
# plain escape to exit.  lint() must return zero fail_routed_to_exit diags.
# Both probes run in the same Python invocation so a whole-scope suppression
# cannot green only one of them.
# ---------------------------------------------------------------------------

set +e
(
    cd "$MODULE_DIR"
    uv run --project . python - <<PYEOF
import sys, os

try:
    from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
    from amplifier_module_loop_pipeline.validation import lint
except Exception as exc:
    print(f"INFRA: import failed: {exc}", file=sys.stderr)
    sys.exit(2)

# ---- Probe 1: hazard graph -------------------------------------------------
# A node (GAMMA) receives a failure-conditioned edge from BETA and has:
#   - one conditional outgoing edge (back to ALPHA)
#   - one plain unconditional outgoing edge (to EXIT)
# The plain edge is the silent escape: if the condition does not match,
# the failure exits green.  lint() must flag this.

hazard = Graph(
    name="hazard",
    nodes={
        "$NODE_ENTRY": Node(id="$NODE_ENTRY", shape="Mdiamond", label="Start"),
        "$NODE_ALPHA": Node(id="$NODE_ALPHA", shape="box", prompt="step"),
        "$NODE_BETA":  Node(id="$NODE_BETA",  shape="parallelogram"),
        "$NODE_GAMMA": Node(id="$NODE_GAMMA", shape="parallelogram"),
        "$NODE_EXIT":  Node(id="$NODE_EXIT",  shape="Msquare", label="Exit"),
    },
    edges=[
        Edge("$NODE_ENTRY", "$NODE_ALPHA"),
        Edge("$NODE_ALPHA", "$NODE_BETA"),
        Edge("$NODE_BETA",  "$NODE_EXIT",  condition="outcome=success"),
        Edge("$NODE_BETA",  "$NODE_GAMMA", condition="outcome=fail"),
        Edge("$NODE_GAMMA", "$NODE_ALPHA", condition="outcome=success"),
        Edge("$NODE_GAMMA", "$NODE_EXIT"),
    ],
)

try:
    hazard_diags = [d for d in lint(hazard) if d.rule == "fail_routed_to_exit"]
except Exception as exc:
    print(f"INFRA: lint() raised on hazard graph: {exc}", file=sys.stderr)
    sys.exit(2)

# ---- Probe 2: true re-gate (mixed-case) ------------------------------------
# A node (RG_GATE) receives a failure-conditioned edge and has ONLY
# conditional outgoing edges — no plain escape to the exit.
# lint() must NOT flag this.

true_regate = Graph(
    name="true_regate",
    nodes={
        "$NODE_RG_ENTRY": Node(id="$NODE_RG_ENTRY", shape="Mdiamond", label="Start"),
        "$NODE_RG_WORK":  Node(id="$NODE_RG_WORK",  shape="box", prompt="step"),
        "$NODE_RG_CHECK": Node(id="$NODE_RG_CHECK", shape="parallelogram"),
        "$NODE_RG_GATE":  Node(id="$NODE_RG_GATE",  shape="parallelogram"),
        "$NODE_RG_BACK":  Node(id="$NODE_RG_BACK",  shape="parallelogram"),
        "$NODE_RG_ALT":   Node(id="$NODE_RG_ALT",   shape="parallelogram"),
        "$NODE_RG_EXIT":  Node(id="$NODE_RG_EXIT",  shape="Msquare", label="Exit"),
    },
    edges=[
        Edge("$NODE_RG_ENTRY", "$NODE_RG_WORK"),
        Edge("$NODE_RG_WORK",  "$NODE_RG_CHECK"),
        Edge("$NODE_RG_CHECK", "$NODE_RG_EXIT",  condition="outcome=success"),
        Edge("$NODE_RG_CHECK", "$NODE_RG_GATE",  condition="outcome=fail"),
        Edge("$NODE_RG_GATE",  "$NODE_RG_BACK",  condition="outcome=success"),
        Edge("$NODE_RG_GATE",  "$NODE_RG_ALT",   condition="outcome=fail"),
    ],
)

try:
    regate_diags = [d for d in lint(true_regate) if d.rule == "fail_routed_to_exit"]
except Exception as exc:
    print(f"INFRA: lint() raised on true-regate graph: {exc}", file=sys.stderr)
    sys.exit(2)

# ---- Verdict ---------------------------------------------------------------
if len(hazard_diags) == 0:
    print("fail_routed_to_exit diagnostic expected but got []")
    sys.exit(1)

if len(regate_diags) != 0:
    print("fail_routed_to_exit false positive: true re-gate shape should not be flagged")
    sys.exit(1)

print("behavioral probes passed: hazard flagged, true re-gate clean")
sys.exit(0)
PYEOF
) >"$PROBE_TMP" 2>&1
PROBE_RC=$?
set -e

if [ "$PROBE_RC" -ge 2 ]; then
    echo "INFRA: behavioral probe exited with rc=$PROBE_RC" >&2
    cat "$PROBE_TMP" >&2
    exit 2
fi

cat "$PROBE_TMP"

if [ "$PROBE_RC" -eq 1 ]; then
    exit 1
fi

# ---------------------------------------------------------------------------
# Test-suite check: run the existing TestFailRoutedToExit class.
# At the base SHA, test_indirect_regating_intermediary_not_flagged asserts
# the wrong expectation (assert not _diag(...)) and passes — but the
# behavioral probe above already exits 1 for the defect.  After a correct
# fix the behavioral probe exits 0 and we reach here; the test class must
# also pass (the wrong-assertion test must have been corrected as part of
# the fix, and any new regression test must pass).
# ---------------------------------------------------------------------------

echo "Running TestFailRoutedToExit test class..."

TEST_TMP="$(mktemp)"
trap 'rm -f "$PROBE_TMP" "$TEST_TMP"' EXIT

set +e
(
    cd "$MODULE_DIR"
    uv run --project . pytest \
        tests/test_topological_lint.py::TestFailRoutedToExit \
        -v --tb=short
) >"$TEST_TMP" 2>&1
TEST_RC=$?
set -e

cat "$TEST_TMP"

if [ "$TEST_RC" -ne 0 ]; then
    echo "fail_routed_to_exit diagnostic expected but got []"
    exit 1
fi

echo "All checks passed: defect is not present."
exit 0
