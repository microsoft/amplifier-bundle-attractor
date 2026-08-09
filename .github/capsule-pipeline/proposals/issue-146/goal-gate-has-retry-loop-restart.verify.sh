set -euo pipefail

# ---------------------------------------------------------------------------
# DEFINITION.verify.sh — gate for goal_gate_has_retry false positive on
# examples/pipelines/00-convergence-loop.dot
#
# RED  (exit 1): lint() on 00-convergence-loop.dot fires goal_gate_has_retry
# GREEN (exit 0): no goal_gate_has_retry diagnostic on that file
#
# Accepts ANY correct fix:
#   - adding retry_target/fallback_retry_target to the example node, OR
#   - teaching the rule to recognise loop_restart edges, OR
#   - any other behavioral change that eliminates the false positive
#     while preserving the rule for genuinely retry-less goal gates.
# ---------------------------------------------------------------------------

# --- Infrastructure guards -------------------------------------------------

if ! command -v python3 >/dev/null 2>&1; then
    echo "INFRA: python3 not found" >&2
    exit 2
fi

REPO_ROOT="$(pwd)"

MODULE_DIR="$REPO_ROOT/modules/loop-pipeline"
if [ ! -d "$MODULE_DIR" ]; then
    echo "INFRA: modules/loop-pipeline not found in $REPO_ROOT" >&2
    exit 2
fi

EXAMPLE_DOT="$REPO_ROOT/examples/pipelines/00-convergence-loop.dot"
if [ ! -f "$EXAMPLE_DOT" ]; then
    echo "INFRA: example file not found: $EXAMPLE_DOT" >&2
    exit 2
fi

TEST_FILE="$MODULE_DIR/tests/test_validation.py"
if [ ! -f "$TEST_FILE" ]; then
    echo "INFRA: test file not found: $TEST_FILE" >&2
    exit 2
fi

# Verify the module is importable from the repo tree (not from ambient install)
PYTHONPATH="$MODULE_DIR" python3 -c "import amplifier_module_loop_pipeline.validation" 2>/dev/null || {
    echo "INFRA: cannot import amplifier_module_loop_pipeline from $MODULE_DIR" >&2
    exit 2
}

# ---------------------------------------------------------------------------
# Step 1: Run the existing positive-case test from test_validation.py
# (asserts the rule still fires when a goal-gate node has no retry mechanism)
# This confirms the rule was not simply removed or broadly suppressed.
# ---------------------------------------------------------------------------

if ! PYTHONPATH="$MODULE_DIR" python3 -m pytest \
        "$TEST_FILE::test_goal_gate_without_retry_target" \
        -q --tb=short 2>&1; then
    echo "INFRA: existing test test_goal_gate_without_retry_target failed unexpectedly" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Step 2: Primary assertion — 00-convergence-loop.dot must not fire
# goal_gate_has_retry.
#
# This is the reported defect: the flagship tutorial example trips its own
# linter with a false-positive goal_gate_has_retry WARNING.  Any correct fix
# — whether it repairs the example or teaches the rule — must eliminate this.
# ---------------------------------------------------------------------------

PRIMARY_RESULT=$(PYTHONPATH="$MODULE_DIR" python3 - <<PYEOF
import sys
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.validation import lint
from pathlib import Path

dot = Path('$EXAMPLE_DOT').read_text(encoding='utf-8')
graph = parse_dot(dot)
diags = lint(graph)
gg_diags = [d for d in diags if d.rule == 'goal_gate_has_retry']
if gg_diags:
    print('FAIL')
    for d in gg_diags:
        print(f'  [{d.severity}] [{d.rule}] {d.message}')
else:
    print('PASS')
PYEOF
)

if echo "$PRIMARY_RESULT" | grep -q '^FAIL'; then
    echo "goal_gate_has_retry false positive on 00-convergence-loop.dot"
    echo "$PRIMARY_RESULT"
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 3: Positive-case preservation — a goal-gate node with no retry
# mechanism (no retry_target/fallback_retry_target attribute AND no
# loop_restart back-edge) must still produce a goal_gate_has_retry warning.
#
# Node names are generated at runtime to prevent name-enumeration dodges.
# Uses the Graph/Node/Edge API to build a minimal linear graph.
# ---------------------------------------------------------------------------

POSITIVE_RESULT=$(PYTHONPATH="$MODULE_DIR" python3 - <<PYEOF
import sys, random, string
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.validation import lint

suffix = ''.join(random.choices(string.ascii_lowercase, k=8))
gate_id = f'gate_linear_{suffix}'
worker_id = f'worker_{suffix}'

nodes = {
    'start':   Node(id='start',   shape='Mdiamond', label='Start'),
    worker_id: Node(id=worker_id, shape='box',      label='Worker'),
    gate_id:   Node(id=gate_id,   shape='parallelogram', label='Gate',
                    attrs={'goal_gate': 'true'}),
    'done':    Node(id='done',    shape='Msquare',  label='Done'),
}
edges = [
    Edge(from_node='start',     to_node=worker_id),
    Edge(from_node=worker_id,   to_node=gate_id),
    Edge(from_node=gate_id,     to_node='done'),
    # No loop_restart edge, no retry_target attr — rule must fire
]
g = Graph(name='linear_test', nodes=nodes, edges=edges)
diags = lint(g)
gg_diags = [d for d in diags if d.rule == 'goal_gate_has_retry' and d.node_id == gate_id]
if not gg_diags:
    print(f'FAIL: goal_gate_has_retry did not fire on linear goal_gate node {gate_id} (rule suppressed too broadly)')
    sys.exit(1)
else:
    print('PASS')
PYEOF
)

if echo "$POSITIVE_RESULT" | grep -q '^FAIL'; then
    echo "goal_gate_has_retry false positive on 00-convergence-loop.dot"
    echo "Positive-case preservation failure (rule over-suppressed):"
    echo "$POSITIVE_RESULT"
    exit 1
fi

echo "OK: goal_gate_has_retry correctly handles 00-convergence-loop.dot"
exit 0
