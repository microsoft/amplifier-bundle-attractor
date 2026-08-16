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

# Every name these probes expose to the code under test -- both GRAPH names and
# every NODE id -- is drawn fresh on each run.  A fix must green this gate by
# recognizing the hazard SHAPE; it cannot green it by recognizing a name.
#
# SUFFIX_A / SUFFIX_B are random per-run tokens.  They name the two probe
# graphs (`g$SUFFIX_A` / `g$SUFFIX_B`) and supply the random tail of probe 1's
# condition literal, so neither the graph names nor the condition text is a
# constant a fix can key on.  Both graph names share ONE shape, so a predicate
# broad enough to match probe 1's name also matches probe 2's -- and flagging
# probe 2 fails the false-positive check below.
SUFFIX_A="${RANDOM}${RANDOM}"
SUFFIX_B="${RANDOM}${RANDOM}"

# Node ids are re-drawn per node, per run, out of the ordinary identifier
# space: a random lowercase-alpha prefix followed by characters drawn from
# [a-z0-9], at a random length.  There is deliberately NO fixed skeleton left
# -- no constant prefix, no constant digit run (an id may carry no digit at
# all), no constant trailing role letter -- so no fixed regex separates a probe
# node id from an author-written one like `start` or `triage`.  A predicate
# broad enough to match every probe id must also match ordinary ids, which
# makes it a general rule rather than a name-specific dodge.
#
# The alphabets are ARRAYS of single characters rather than one packed string
# on purpose: a 36-character run of [A-Za-z0-9] is precisely the shape this
# repo's capsule-artifact scan blocks as `high-entropy-token`, and a capsule
# artifact is never redacted in place (see scrub_secrets.py).
_ALPHA=(a b c d e f g h i j k l m n o p q r s t u v w x y z)
_DIGIT=(0 1 2 3 4 5 6 7 8 9)

_rand_id() {
    local len=$((6 + RANDOM % 6))
    local plen=$((2 + RANDOM % 3))
    local out=""
    local i
    for ((i = 0; i < len; i++)); do
        if [ "$i" -lt "$plen" ] || [ $((RANDOM % 4)) -gt 0 ]; then
            out="${out}${_ALPHA[$((RANDOM % 26))]}"
        else
            out="${out}${_DIGIT[$((RANDOM % 10))]}"
        fi
    done
    printf '%s' "$out"
}

# Draw DISTINCT ids into the named shell variables.  Distinctness is not
# cosmetic: a collision would silently merge two nodes in the graph dict and
# make the probe's verdict depend on the draw.
_draw_ids() {
    local seen=" "
    local name id
    for name in "$@"; do
        while :; do
            id="$(_rand_id)"
            case "$seen" in
                *" $id "*) continue ;;
            esac
            break
        done
        seen="$seen$id "
        printf -v "$name" '%s' "$id"
    done
}

_draw_ids NODE_ENTRY NODE_ALPHA NODE_BETA NODE_GAMMA NODE_EXIT
_draw_ids NODE_RG_ENTRY NODE_RG_WORK NODE_RG_CHECK NODE_RG_GATE \
          NODE_RG_BACK NODE_RG_ALT NODE_RG_EXIT

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
#
# GAMMA's conditional edge carries the issue's OWN condition shape
# (context.tool.last_line=..., as in issue #197's triage -> work edge) with a
# random per-run tail, so the probe is faithful to the reported repro and the
# literal is not a constant a fix can special-case.

hazard = Graph(
    name="g$SUFFIX_A",
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
        Edge("$NODE_GAMMA", "$NODE_ALPHA", condition="context.tool.last_line=r$SUFFIX_B"),
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
    name="g$SUFFIX_B",
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
