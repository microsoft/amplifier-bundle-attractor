set -euo pipefail
# DEFINITION.verify.sh -- Issue #268: Lint is silent on unknown node shapes
#
# Exits 0 (GREEN) when the definition of done is satisfied.
# Exits 1 (RED)   when the defect is present -- i.e. at the base SHA da8ffd1.
# Exits 2         on infrastructure problems (missing tooling, bad environment).
#
# Run from the repository root:
#   bash .ai/capsule/DEFINITION.verify.sh

# ---------------------------------------------------------------------------
# Locate the module directory relative to the invoking repo root (cwd).
# The script is invoked as: bash .ai/capsule/DEFINITION.verify.sh
# with the repo root as cwd -- resolve everything from there.
# ---------------------------------------------------------------------------
REPO_ROOT="$(pwd)"
MODULE_DIR="$REPO_ROOT/modules/loop-pipeline"

if [ ! -d "$MODULE_DIR" ]; then
    echo "INFRA: modules/loop-pipeline not found under $REPO_ROOT" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Require uv -- the repo's own convention for isolated, self-provisioning runs.
# ---------------------------------------------------------------------------
if ! command -v uv &>/dev/null; then
    echo "INFRA: uv not found; install uv to run this gate" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Require python3 as a sanity check.
# ---------------------------------------------------------------------------
if ! command -v python3 &>/dev/null; then
    echo "INFRA: python3 not found" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Generate runtime-born, semantically neutral probe identifiers.
# Names are random alphanumeric strings with no domain vocabulary from the
# issue subject space (no shape names, handler names, or feature tokens).
# The invalid shapes are random hex strings guaranteed not in SHAPE_TO_HANDLER.
# Node IDs are similarly random so no name-enumeration dodge is possible.
# ---------------------------------------------------------------------------
R1=$RANDOM$RANDOM
R2=$RANDOM$RANDOM
R3=$RANDOM$RANDOM
R4=$RANDOM$RANDOM
R5=$RANDOM$RANDOM
R6=$RANDOM$RANDOM
# Node IDs: random alphanumeric, no domain tokens
NODE_BAD_TC="n${R1}"      # unknown shape + has tool_command
NODE_BAD_NO_TC="n${R2}"   # unknown shape + no tool_command, no prompt (catches tool_command-only fixes)
NODE_BAD_PROMPT="n${R6}"  # unknown shape + has non-empty prompt, no tool_command (catches prompt/label-conditioned fixes)
NODE_GOOD="n${R3}"
NODE_START="n${R4}"
NODE_EXIT="n${R5}"
# Invalid shapes: random hex prefix + suffix, guaranteed not in SHAPE_TO_HANDLER
INVALID_SHAPE_A="xq${R1}zv"
INVALID_SHAPE_B="wm${R2}yk"
INVALID_SHAPE_C="pf${R6}rn"
GRAPH_NAME="g${R3}${R4}"
# Runtime-born non-empty prompt value: no domain vocabulary
PROMPT_VAL="do${R6}work"

# ---------------------------------------------------------------------------
# PROBE: inline Python assertions via uv run (self-provisions deps, hermetic).
# ---------------------------------------------------------------------------
# We run the probe as a uv-managed script from MODULE_DIR so that uv resolves
# the module's own declared dependencies from the project tree -- never from
# an ambient venv or site-packages install.
# ---------------------------------------------------------------------------

PROBE_RC=0
PROBE_OUTPUT="$(
  cd "$MODULE_DIR"
  NODE_BAD_TC="$NODE_BAD_TC" \
  NODE_BAD_NO_TC="$NODE_BAD_NO_TC" \
  NODE_BAD_PROMPT="$NODE_BAD_PROMPT" \
  NODE_GOOD="$NODE_GOOD" \
  NODE_START="$NODE_START" \
  NODE_EXIT="$NODE_EXIT" \
  INVALID_SHAPE_A="$INVALID_SHAPE_A" \
  INVALID_SHAPE_B="$INVALID_SHAPE_B" \
  INVALID_SHAPE_C="$INVALID_SHAPE_C" \
  GRAPH_NAME="$GRAPH_NAME" \
  PROMPT_VAL="$PROMPT_VAL" \
  uv run python3 - <<'PYEOF'
import os
import sys

# Infrastructure: import the module under test.
# Any ImportError here is an environment problem, not a defect.
try:
    from amplifier_module_loop_pipeline.graph import Node, Edge, Graph
    from amplifier_module_loop_pipeline.validation import (
        lint,
        SHAPE_TO_HANDLER,
    )
except ImportError as e:
    print(f"INFRA: import failed: {e}", file=sys.stderr)
    sys.exit(2)

# ---------------------------------------------------------------------------
# Retrieve runtime-generated probe identifiers from environment.
# ---------------------------------------------------------------------------
NODE_BAD_TC     = os.environ["NODE_BAD_TC"]
NODE_BAD_NO_TC  = os.environ["NODE_BAD_NO_TC"]
NODE_BAD_PROMPT = os.environ["NODE_BAD_PROMPT"]
NODE_GOOD       = os.environ["NODE_GOOD"]
NODE_START      = os.environ["NODE_START"]
NODE_EXIT       = os.environ["NODE_EXIT"]
INVALID_SHAPE_A = os.environ["INVALID_SHAPE_A"]
INVALID_SHAPE_B = os.environ["INVALID_SHAPE_B"]
INVALID_SHAPE_C = os.environ["INVALID_SHAPE_C"]
GRAPH_NAME      = os.environ["GRAPH_NAME"]
PROMPT_VAL      = os.environ["PROMPT_VAL"]

# Sanity: the generated shapes must not accidentally be known shapes.
for shape_var, shape_val in [
    ("INVALID_SHAPE_A", INVALID_SHAPE_A),
    ("INVALID_SHAPE_B", INVALID_SHAPE_B),
    ("INVALID_SHAPE_C", INVALID_SHAPE_C),
]:
    if shape_val in SHAPE_TO_HANDLER:
        print(f"INFRA: generated shape {shape_val!r} is in SHAPE_TO_HANDLER; re-run.", file=sys.stderr)
        sys.exit(2)

# Sanity: confirm known shapes are known (infrastructure check).
if "parallelogram" not in SHAPE_TO_HANDLER or "Mdiamond" not in SHAPE_TO_HANDLER:
    print("INFRA: SHAPE_TO_HANDLER missing expected known shapes; import may be wrong.", file=sys.stderr)
    sys.exit(2)

# Sanity: PROMPT_VAL must be non-empty (it is our runtime-born prompt string).
if not PROMPT_VAL:
    print("INFRA: PROMPT_VAL is empty; re-run.", file=sys.stderr)
    sys.exit(2)

# ---------------------------------------------------------------------------
# Mixed-scope probe: a graph containing:
#   NODE_BAD_TC     -- unknown shape, no explicit type, HAS tool_command
#   NODE_BAD_NO_TC  -- unknown shape, no explicit type, NO tool_command, no prompt
#   NODE_BAD_PROMPT -- unknown shape, no explicit type, HAS non-empty prompt, no tool_command
#   NODE_GOOD       -- known shape (parallelogram), no explicit type
#
# This is the mixed-scope requirement:
#   - All three bad nodes must trigger an ERROR (unknown shape, no explicit type)
#   - The good node must NOT trigger an ERROR for unknown shape
#   - A fix that only diagnoses nodes with tool_command stays RED (NODE_BAD_NO_TC, NODE_BAD_PROMPT)
#   - A fix conditioned on 'not has_prompt and not has_label' stays RED (NODE_BAD_PROMPT has a prompt)
#   - A whole-graph suppression stays RED (NODE_GOOD would be collateral)
# ---------------------------------------------------------------------------
try:
    start       = Node(id=NODE_START,      shape="Mdiamond",       label="Start")
    bad_tc      = Node(id=NODE_BAD_TC,     shape=INVALID_SHAPE_A,  attrs={"tool_command": "./x.sh"})
    bad_no_tc   = Node(id=NODE_BAD_NO_TC,  shape=INVALID_SHAPE_B)
    bad_prompt  = Node(id=NODE_BAD_PROMPT, shape=INVALID_SHAPE_C,  prompt=PROMPT_VAL)
    good_node   = Node(id=NODE_GOOD,       shape="parallelogram",   attrs={"tool_command": "./ok.sh"})
    exit_node   = Node(id=NODE_EXIT,       shape="Msquare",         label="Exit")
    g = Graph(
        name=GRAPH_NAME,
        nodes={
            NODE_START:      start,
            NODE_BAD_TC:     bad_tc,
            NODE_BAD_NO_TC:  bad_no_tc,
            NODE_BAD_PROMPT: bad_prompt,
            NODE_GOOD:       good_node,
            NODE_EXIT:       exit_node,
        },
        edges=[
            Edge(from_node=NODE_START,      to_node=NODE_BAD_TC),
            Edge(from_node=NODE_BAD_TC,     to_node=NODE_BAD_NO_TC),
            Edge(from_node=NODE_BAD_NO_TC,  to_node=NODE_BAD_PROMPT),
            Edge(from_node=NODE_BAD_PROMPT, to_node=NODE_GOOD),
            Edge(from_node=NODE_GOOD,       to_node=NODE_EXIT),
        ],
    )
except Exception as exc:
    print(f"INFRA: could not construct probe graph: {exc}", file=sys.stderr)
    sys.exit(2)

# ---------------------------------------------------------------------------
# Run lint() once on the mixed-scope graph.
# ---------------------------------------------------------------------------
try:
    diags = lint(g)
except Exception as exc:
    # An exception from lint() itself is an observed failure (not infra):
    # lint() must not raise -- it must return diagnostics.
    print(
        f"lint() produced no ERROR for node with unknown shape and no explicit type: "
        f"lint() raised unexpectedly instead of returning diagnostics: {exc}"
    )
    sys.exit(1)

error_diags = [d for d in diags if d.severity == "ERROR"]

# ---------------------------------------------------------------------------
# CHECK 1a: lint() produces at least one ERROR for the unknown-shape node
#           that HAS tool_command (NODE_BAD_TC).
# ---------------------------------------------------------------------------
bad_tc_errors = [
    d for d in error_diags
    if getattr(d, "node_id", "") == NODE_BAD_TC or NODE_BAD_TC in d.message
]

if not bad_tc_errors:
    all_diags = [(d.severity, getattr(d, "rule", "?"), d.message) for d in diags]
    print(
        f"lint() produced no ERROR for node with unknown shape and no explicit type: "
        f"node {NODE_BAD_TC!r} has shape {INVALID_SHAPE_A!r} (not in SHAPE_TO_HANDLER), "
        f"no explicit type, and has tool_command -- but got {len(error_diags)} total ERROR(s), "
        f"none associated with that node. All diagnostics: {all_diags}"
    )
    sys.exit(1)

print(f"CHECK 1a PASSED: {len(bad_tc_errors)} ERROR(s) for unknown-shape+tool_command node {NODE_BAD_TC!r}.")

# ---------------------------------------------------------------------------
# CHECK 1b: lint() produces at least one ERROR for the unknown-shape node
#           that has NO tool_command and NO prompt (NODE_BAD_NO_TC).
#           This check catches a tool_command-only fix: a fix that only
#           diagnoses unknown-shape nodes that carry tool_command stays RED
#           here, because NODE_BAD_NO_TC has no tool_command attribute.
# ---------------------------------------------------------------------------
bad_no_tc_errors = [
    d for d in error_diags
    if getattr(d, "node_id", "") == NODE_BAD_NO_TC or NODE_BAD_NO_TC in d.message
]

if not bad_no_tc_errors:
    all_diags = [(d.severity, getattr(d, "rule", "?"), d.message) for d in diags]
    print(
        f"lint() produced no ERROR for node with unknown shape and no explicit type: "
        f"node {NODE_BAD_NO_TC!r} has shape {INVALID_SHAPE_B!r} (not in SHAPE_TO_HANDLER), "
        f"no explicit type, and NO tool_command -- but got {len(error_diags)} total ERROR(s), "
        f"none associated with that node. All diagnostics: {all_diags}"
    )
    sys.exit(1)

print(f"CHECK 1b PASSED: {len(bad_no_tc_errors)} ERROR(s) for unknown-shape+no-tool_command node {NODE_BAD_NO_TC!r}.")

# ---------------------------------------------------------------------------
# CHECK 1c: lint() produces at least one ERROR for the unknown-shape node
#           that HAS a non-empty prompt and NO tool_command (NODE_BAD_PROMPT).
#           This check catches a prompt/label-conditioned fix: a fix that only
#           diagnoses unknown-shape nodes that lack a prompt or label stays RED
#           here, because NODE_BAD_PROMPT has a non-empty prompt value.
#           The issue requires: unknown shape + no explicit type => ERROR,
#           unconditionally -- regardless of whether the node has a prompt.
# ---------------------------------------------------------------------------
bad_prompt_errors = [
    d for d in error_diags
    if getattr(d, "node_id", "") == NODE_BAD_PROMPT or NODE_BAD_PROMPT in d.message
]

if not bad_prompt_errors:
    all_diags = [(d.severity, getattr(d, "rule", "?"), d.message) for d in diags]
    print(
        f"lint() produced no ERROR for node with unknown shape and no explicit type: "
        f"node {NODE_BAD_PROMPT!r} has shape {INVALID_SHAPE_C!r} (not in SHAPE_TO_HANDLER), "
        f"no explicit type, and a non-empty prompt -- but got {len(error_diags)} total ERROR(s), "
        f"none associated with that node. All diagnostics: {all_diags}"
    )
    sys.exit(1)

print(f"CHECK 1c PASSED: {len(bad_prompt_errors)} ERROR(s) for unknown-shape+prompt node {NODE_BAD_PROMPT!r}.")

# ---------------------------------------------------------------------------
# CHECK 2 (mixed-scope): The known-shape node in the SAME graph does NOT
# get an ERROR diagnostic for having an unknown shape.
# This ensures the rule fires per-node, not for the whole graph the moment
# any unknown shape is present.
# ---------------------------------------------------------------------------
good_node_shape_errors = [
    d for d in error_diags
    if getattr(d, "node_id", "") == NODE_GOOD or NODE_GOOD in d.message
]

# To distinguish shape-related errors from other structural errors on NODE_GOOD,
# build a graph with ONLY the good node and check whether the same errors appear.
try:
    g_good_only = Graph(
        name=GRAPH_NAME + "g",
        nodes={
            NODE_START: Node(id=NODE_START, shape="Mdiamond",     label="Start"),
            NODE_GOOD:  Node(id=NODE_GOOD,  shape="parallelogram", attrs={"tool_command": "./ok.sh"}),
            NODE_EXIT:  Node(id=NODE_EXIT,  shape="Msquare",       label="Exit"),
        },
        edges=[
            Edge(from_node=NODE_START, to_node=NODE_GOOD),
            Edge(from_node=NODE_GOOD,  to_node=NODE_EXIT),
        ],
    )
    diags_good_only = lint(g_good_only)
except Exception as exc:
    print(f"INFRA: could not run lint on good-only graph: {exc}", file=sys.stderr)
    sys.exit(2)

errors_good_only = [d for d in diags_good_only if d.severity == "ERROR"]
shape_errors_good_only = [
    d for d in errors_good_only
    if NODE_GOOD in d.message or getattr(d, "node_id", "") == NODE_GOOD
]
if shape_errors_good_only:
    print(
        f"lint() produced no ERROR for node with unknown shape and no explicit type: "
        f"known-shape node {NODE_GOOD!r} (parallelogram) got ERROR(s) even in isolation: "
        f"{[(d.severity, getattr(d, 'rule', '?'), d.message) for d in shape_errors_good_only]}"
    )
    sys.exit(1)

print(f"CHECK 2 PASSED: known-shape node {NODE_GOOD!r} (parallelogram) not flagged for unknown shape.")

# ---------------------------------------------------------------------------
# CHECK 3: Nodes with an explicit type= attribute are NOT flagged by the
#          unknown-shape rule.  The existing type-checking rule already handles
#          unknown type values; the new rule targets only the shape-only path.
#          (This check does NOT require validate_or_raise() to raise; it only
#          checks that lint() does not fire the shape rule on a typed node.)
# ---------------------------------------------------------------------------
R_TYPED = NODE_BAD_TC + "t"
try:
    g_typed = Graph(
        name=GRAPH_NAME + "t",
        nodes={
            NODE_START: Node(id=NODE_START, shape="Mdiamond",     label="Start"),
            R_TYPED:    Node(id=R_TYPED,    shape=INVALID_SHAPE_A, type="tool",
                             attrs={"tool_command": "./x.sh"}),
            NODE_EXIT:  Node(id=NODE_EXIT,  shape="Msquare",      label="Exit"),
        },
        edges=[
            Edge(from_node=NODE_START, to_node=R_TYPED),
            Edge(from_node=R_TYPED,    to_node=NODE_EXIT),
        ],
    )
    typed_diags = lint(g_typed)
except Exception as exc:
    print(f"INFRA: could not run lint() on typed graph: {exc}", file=sys.stderr)
    sys.exit(2)

# Any ERROR on R_TYPED that disappears when we give the same node a known shape
# is shape-related and must not fire when type= is explicitly set.
shape_errors_on_typed = [
    d for d in typed_diags
    if d.severity == "ERROR"
    and (getattr(d, "node_id", "") == R_TYPED or R_TYPED in d.message)
    and getattr(d, "rule", "") not in ("type_known",)
]
if shape_errors_on_typed:
    try:
        g_known_typed = Graph(
            name=GRAPH_NAME + "kt",
            nodes={
                NODE_START: Node(id=NODE_START, shape="Mdiamond",     label="Start"),
                R_TYPED:    Node(id=R_TYPED,    shape="parallelogram", type="tool",
                                 attrs={"tool_command": "./x.sh"}),
                NODE_EXIT:  Node(id=NODE_EXIT,  shape="Msquare",      label="Exit"),
            },
            edges=[
                Edge(from_node=NODE_START, to_node=R_TYPED),
                Edge(from_node=R_TYPED,    to_node=NODE_EXIT),
            ],
        )
        kt_diags = lint(g_known_typed)
        kt_errors = [
            d for d in kt_diags
            if d.severity == "ERROR"
            and (getattr(d, "node_id", "") == R_TYPED or R_TYPED in d.message)
        ]
        if not kt_errors:
            print(
                f"lint() produced no ERROR for node with unknown shape and no explicit type: "
                f"unknown-shape rule incorrectly fired on a node with explicit type='tool'. "
                f"The rule must target the shape-only path (no explicit type). "
                f"Errors: {[(d.severity, getattr(d, 'rule', '?'), d.message) for d in shape_errors_on_typed]}"
            )
            sys.exit(1)
    except Exception as exc:
        print(f"INFRA: could not run lint() on known-typed graph: {exc}", file=sys.stderr)
        sys.exit(2)

print("CHECK 3 PASSED: unknown-shape rule does not fire when node has explicit type attribute.")

# ---------------------------------------------------------------------------
# All probe checks passed.
# ---------------------------------------------------------------------------
print()
print("PROBE CHECKS PASSED.")
sys.exit(0)
PYEOF
)" || PROBE_RC=$?

PROBE_RC="${PROBE_RC:-0}"

if [ "$PROBE_RC" -eq 2 ]; then
    echo "INFRA: probe exited with infrastructure error (rc=2)" >&2
    echo "$PROBE_OUTPUT" >&2
    exit 2
fi

if [ "$PROBE_RC" -ne 0 ]; then
    # rc=1: assertion failure -- print the output (contains the red_signal substring)
    echo "$PROBE_OUTPUT"
    exit 1
fi

echo "$PROBE_OUTPUT"

# ---------------------------------------------------------------------------
# REGRESSION TEST CHECK: the fix must include at least one real test in the
# repo's own test suite that exercises the reported behavior (unknown shape +
# no explicit type => ERROR at lint time).
# We run the full test_validation.py suite via the repo's own uv convention.
# A correct fix includes a passing regression test; if it does not, the suite
# still passes but the DoD is not met -- the probe above is the behavioral gate.
#   exit 0 -> all tests collected and passed -> GREEN
#   exit 1 -> tests failed                   -> RED
#   exit 5 -> no tests collected             -> RED (suite is empty, unexpected)
# ---------------------------------------------------------------------------
echo ""
echo "Running validation test suite (uv run pytest tests/test_validation.py -q)..."

cd "$MODULE_DIR"
PYTEST_OUTPUT="$(uv run pytest tests/test_validation.py -q 2>&1)" || PYTEST_RC=$?
PYTEST_RC="${PYTEST_RC:-0}"

if [ "$PYTEST_RC" -eq 5 ]; then
    echo "lint() produced no ERROR for node with unknown shape and no explicit type: no tests collected from tests/test_validation.py -- the test suite is unexpectedly empty."
    exit 1
fi

if [ "$PYTEST_RC" -ne 0 ]; then
    echo "lint() produced no ERROR for node with unknown shape and no explicit type: validation test suite has failures."
    echo "$PYTEST_OUTPUT"
    exit 1
fi

echo "$PYTEST_OUTPUT"
echo ""
echo "ALL CHECKS PASSED -- definition of done is satisfied."
exit 0
