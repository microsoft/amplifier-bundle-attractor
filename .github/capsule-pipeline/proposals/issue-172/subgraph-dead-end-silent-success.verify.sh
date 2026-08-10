set -euo pipefail

# ---------------------------------------------------------------------------
# Gate: run_subgraph dead-end (conditional mismatch) must return status=fail
# with a non-empty failure_reason, not status=success.
#
# RED  (exit 1): run_subgraph returns status='success' for a dead-ended node
#                whose only outgoing edge cannot match the current outcome
#                (conditional mismatch); OR returns status='fail' but with
#                an empty/None failure_reason (incomplete repair); OR the
#                dead-end node ran more than once (safety-bound loop dodge);
#                OR the composed parent (parallel pipeline) finishes
#                status='success' when a branch dead-ended, with ZERO
#                failure signal anywhere (no non-success parent outcome, no
#                failed branch entry carrying a non-empty failure_reason in
#                the recorded parallel results, no failure-carrying event);
#                OR an identical all-clean parallel pipeline no longer
#                finishes status='success' (legitimate parallel success
#                destroyed); OR the repo's own on-topic tests (subgraph
#                runner / parallel / fan-in) fail.
# GREEN (exit 0): run_subgraph returns status='fail' with a non-empty
#                 failure_reason for a conditional-mismatch dead end AND the
#                 dead-end node ran exactly once, AND normal subgraph
#                 completion still returns status='success', AND a parallel
#                 pipeline whose branch dead-ends surfaces that failure
#                 loudly somewhere (parent not status='success', OR a failed
#                 branch entry with a non-empty failure_reason in the
#                 recorded results/events), AND an all-clean parallel
#                 pipeline still finishes status='success', AND the
#                 folded-in existing tests pass.
# INFRA (exit 2): a prerequisite is missing or the environment cannot run
#                 (missing module tree, python, or pytest). Any check that
#                 observes the behavior of the code under test exits 1 on
#                 failure, never 2.
# ---------------------------------------------------------------------------

# Resolve the repo root from git itself, anchored at this script's own
# directory. The gate ships in TWO layouts and must work from both: the
# committed proposals dir (.github/capsule-pipeline/proposals/issue-172/)
# and the runtime layout the pipeline copies it into (.ai/capsule/). The
# previous relative "../.." was correct only for the runtime layout; from
# the committed layout it resolved to .github/capsule-pipeline and the
# gate died INFRA (exit 2) instead of measuring the defect.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! REPO_ROOT="$(cd "$SCRIPT_DIR" && git rev-parse --show-toplevel 2>/dev/null)"; then
    echo "INFRA: cannot resolve the repository root (git rev-parse --show-toplevel failed from $SCRIPT_DIR)" >&2
    exit 2
fi
MODULE_SRC="$REPO_ROOT/modules/loop-pipeline"
TESTS_DIR="$MODULE_SRC/tests"

# --- Infrastructure guards --------------------------------------------------

if [ ! -d "$MODULE_SRC" ]; then
    echo "INFRA: loop-pipeline module not found at $MODULE_SRC" >&2
    exit 2
fi

if [ ! -d "$TESTS_DIR" ]; then
    echo "INFRA: tests directory not found at $TESTS_DIR" >&2
    exit 2
fi

if [ ! -f "$TESTS_DIR/conftest.py" ]; then
    echo "INFRA: conftest.py not found at $TESTS_DIR/conftest.py" >&2
    exit 2
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "INFRA: python3 not found in PATH" >&2
    exit 2
fi

if ! "$PYTHON" -c "import pytest" >/dev/null 2>&1; then
    echo "INFRA: pytest is not importable by $PYTHON -- required to run the repo's own on-topic tests" >&2
    exit 2
fi

ENGINE_FILE="$MODULE_SRC/amplifier_module_loop_pipeline/engine.py"
if [ ! -f "$ENGINE_FILE" ]; then
    echo "INFRA: engine.py not found at $ENGINE_FILE" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Runtime-generated identifiers -- prevents name-enumeration workarounds.
# A patch that hardcodes a skip-list of our probe names or graph names
# cannot enumerate names that do not exist until the gate runs.
# ---------------------------------------------------------------------------
SUFFIX="${RANDOM}_${RANDOM}"
DEAD_END_NODE="dead_end_${SUFFIX}"
UNREACHABLE_NODE="unreachable_${SUFFIX}"
GOOD_NODE="good_${SUFFIX}"
GOOD_EXIT_NODE="good_exit_${SUFFIX}"
GRAPH_NAME="gate_probe_${SUFFIX}"
# Parallel probe identifiers (probe 3)
PAR_FAN_OUT="fan_out_${SUFFIX}"
PAR_DEAD_BRANCH="dead_branch_${SUFFIX}"
PAR_DEAD_NEXT="dead_next_${SUFFIX}"
PAR_GOOD_BRANCH="good_branch_${SUFFIX}"
PAR_FAN_IN="fan_in_${SUFFIX}"
PAR_DONE="done_${SUFFIX}"
PAR_GRAPH_NAME="gate_par_probe_${SUFFIX}"

# ---------------------------------------------------------------------------
# Probe script -- generated by Python to avoid shell heredoc collisions
# with Python's own brace syntax.  All runtime values are substituted before
# the probe runs.
# ---------------------------------------------------------------------------
# The runtime layout guarantees .ai/capsule exists (the script lives there);
# from the committed layout it may not -- create it so mktemp cannot fail.
mkdir -p "$REPO_ROOT/.ai/capsule"
PROBE_SCRIPT="$(mktemp "$REPO_ROOT/.ai/capsule/probe_XXXXXX.py")"
trap 'rm -f "$PROBE_SCRIPT"' EXIT

"$PYTHON" - "$PROBE_SCRIPT" "$MODULE_SRC" "$TESTS_DIR" \
    "$DEAD_END_NODE" "$UNREACHABLE_NODE" \
    "$GOOD_NODE" "$GOOD_EXIT_NODE" \
    "$GRAPH_NAME" \
    "$PAR_FAN_OUT" "$PAR_DEAD_BRANCH" "$PAR_DEAD_NEXT" \
    "$PAR_GOOD_BRANCH" "$PAR_FAN_IN" "$PAR_DONE" \
    "$PAR_GRAPH_NAME" << 'GENEOF'
import sys

(probe_path, module_src, tests_dir,
 dead_end_node, unreachable_node,
 good_node, good_exit_node,
 graph_name,
 par_fan_out, par_dead_branch, par_dead_next,
 par_good_branch, par_fan_in, par_done,
 par_graph_name) = sys.argv[1:]

script = f'''"""
Gate probe: run_subgraph dead-end behavior.

Three probes, all using runtime-generated node and graph names:

Probe 1 (conditional-mismatch dead end + single-execution guard):
  run_subgraph on a node whose only outgoing edge has condition="outcome=fail"
  but the node succeeds => edge selection finds no matching edge => dead end.
  Expected after fix: status=fail WITH a non-empty failure_reason AND the
  dead-end node ran exactly once (not 250 times via a safety-bound loop).
  At base SHA: status=success => assertion fails => exit 1.

Probe 2 (mixed-scope / whole-scope suppression guard):
  The SAME graph also contains a node that completes normally (reaches an exit
  node via an unconditional edge). run_subgraph on that node must still return
  status=success. This prevents a whole-scope suppression (returning FAIL for
  every run_subgraph call) from greening the gate.

Probe 3 (composed parent visibility -- parallel branch surface):
  A full parallel pipeline is constructed with two branches, both of which
  have a path to a shared fan-in node (valid topology):
    - dead branch: PAR_DEAD_BRANCH -> PAR_DEAD_NEXT [condition="outcome=fail"]
                   PAR_DEAD_NEXT -> PAR_FAN_IN
      PAR_DEAD_BRANCH succeeds; its only outgoing edge requires outcome=fail
      => dead end at PAR_DEAD_BRANCH (PAR_DEAD_NEXT never runs).
    - good branch: PAR_GOOD_BRANCH -> PAR_FAN_IN (unconditional)
  Both branches share PAR_FAN_IN as a common reachable node, so the engine
  can find the fan-in and actually exercise the composition behavior.
  The gate pre-checks that the fan-in topology is valid before running.
  The overall pipeline is run via engine.run(). The report's own minimum is
  asserted: the run must not finish status=success with ZERO failure signal
  in outcomes, results, or events. Satisfied by EITHER resolution design:
    (a) the parent finishes non-success, OR
    (b) the parent succeeds but the dead-ended branch's failure is loudly
        recorded -- a branch entry with status=fail and a non-empty
        failure_reason in the stored parallel results, or an emitted event
        carrying the same.
  At base SHA: the dead-ended branch returns success to the fan-in, the
  parent pipeline exits status=success, and no failure signal exists
  anywhere -- this probe fires.

Probe 4 (parallel-success preservation -- positive companion to probe 3):
  An identical all-clean parallel pipeline (same topology, the dead
  branch's edge made unconditional so it completes normally) must still
  finish status=success. A change that makes every parallel pipeline fail
  cannot green this gate.
"""
import asyncio
import sys

# Hermetic: resolve from the invoking repo tree, never from site-packages.
_MODULE_SRC = {module_src!r}
_TESTS_DIR = {tests_dir!r}
for _p in (_MODULE_SRC, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Load the repo\\'s own optional-dep stubs before importing the module.
import conftest  # noqa: F401

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus

# Runtime-generated identifiers (injected by the shell script).
DEAD_END_NODE = {dead_end_node!r}
UNREACHABLE_NODE = {unreachable_node!r}
GOOD_NODE = {good_node!r}
GOOD_EXIT_NODE = {good_exit_node!r}
GRAPH_NAME = {graph_name!r}
PAR_FAN_OUT = {par_fan_out!r}
PAR_DEAD_BRANCH = {par_dead_branch!r}
PAR_DEAD_NEXT = {par_dead_next!r}
PAR_GOOD_BRANCH = {par_good_branch!r}
PAR_FAN_IN = {par_fan_in!r}
PAR_DONE = {par_done!r}
PAR_GRAPH_NAME = {par_graph_name!r}


class _CountingHandler:
    """Counts per-node calls and always succeeds -- deterministic, no backend."""

    def __init__(self):
        self.call_counts: dict = {{}}

    async def execute(self, node, context, graph, logs_root, *, engine=None):
        self.call_counts[node.id] = self.call_counts.get(node.id, 0) + 1
        return Outcome(status=StageStatus.SUCCESS, notes=f"ok: {{node.id}}")


class _EventCollector:
    """Captures every event the engine emits (hooks protocol: async emit)."""

    def __init__(self):
        self.events: list = []

    async def emit(self, event_name, data):
        self.events.append((event_name, dict(data)))


def _event_signals_failure(event_name, data):
    """True if an emitted event loudly carries a failure signal."""
    if data.get("status") == "fail" and data.get("failure_reason"):
        return True
    if "error" in event_name and (
        data.get("failure_reason") or data.get("error") or data.get("reason")
    ):
        return True
    return False


def _make_engine(graph, counting_handler=None):
    """Build an engine over the given graph with a temp logs dir."""
    import tempfile
    handler = counting_handler if counting_handler is not None else _CountingHandler()
    registry = HandlerRegistry(HandlerContext())
    registry.register("codergen", handler)
    tmp = tempfile.mkdtemp()
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=registry,
        logs_root=tmp,
    )
    engine._initialize_context(goal="gate-probe")
    return engine, handler


# ---------------------------------------------------------------------------
# Mixed-scope graph (probes 1 and 2):
#
#   start -> DEAD_END_NODE -> UNREACHABLE_NODE [condition="outcome=fail"]
#   start -> GOOD_NODE -> GOOD_EXIT_NODE [shape=Msquare]
#
# DEAD_END_NODE succeeds; its only outgoing edge requires outcome=fail =>
# no matching edge => conditional-mismatch dead end.
#
# GOOD_NODE succeeds; its outgoing edge is unconditional => reaches
# GOOD_EXIT_NODE (an exit node) => normal completion.
#
# Both nodes share the same graph, so a fix that changes ALL run_subgraph
# returns to FAIL would break the GOOD_NODE probe.
# Graph name is runtime-generated to prevent name-enumeration workarounds.
# ---------------------------------------------------------------------------
def _make_mixed_graph():
    return Graph(
        name=GRAPH_NAME,
        nodes={{
            "start": Node(id="start", shape="Mdiamond"),
            DEAD_END_NODE: Node(id=DEAD_END_NODE, shape="box", prompt="dead end work"),
            UNREACHABLE_NODE: Node(id=UNREACHABLE_NODE, shape="Msquare"),
            GOOD_NODE: Node(id=GOOD_NODE, shape="box", prompt="good work"),
            GOOD_EXIT_NODE: Node(id=GOOD_EXIT_NODE, shape="Msquare"),
        }},
        edges=[
            Edge(from_node="start", to_node=DEAD_END_NODE),
            # DEAD_END_NODE\\'s only outgoing edge requires outcome=fail;
            # DEAD_END_NODE succeeds => no matching edge => dead end.
            Edge(from_node=DEAD_END_NODE, to_node=UNREACHABLE_NODE, condition="outcome=fail"),
            Edge(from_node="start", to_node=GOOD_NODE),
            # GOOD_NODE\\'s outgoing edge is unconditional => normal completion.
            Edge(from_node=GOOD_NODE, to_node=GOOD_EXIT_NODE),
        ],
    )


# ---------------------------------------------------------------------------
# Parallel graph (probe 3):
#
# Topology (both branches share PAR_FAN_IN as a common reachable node):
#
#   start -> PAR_FAN_OUT [shape=component]
#   PAR_FAN_OUT -> PAR_DEAD_BRANCH
#   PAR_FAN_OUT -> PAR_GOOD_BRANCH
#
#   PAR_DEAD_BRANCH -> PAR_DEAD_NEXT [condition="outcome=fail"]
#   PAR_DEAD_NEXT -> PAR_FAN_IN          <-- PAR_DEAD_NEXT is a plain box,
#                                             not an exit; both branches can
#                                             reach PAR_FAN_IN in the graph
#
#   PAR_GOOD_BRANCH -> PAR_FAN_IN        <-- unconditional
#   PAR_FAN_IN -> PAR_DONE [shape=Msquare]
#
# At runtime:
#   PAR_DEAD_BRANCH succeeds; its only outgoing edge requires outcome=fail =>
#   dead end at PAR_DEAD_BRANCH (PAR_DEAD_NEXT never actually runs).
#   PAR_GOOD_BRANCH succeeds and reaches PAR_FAN_IN normally.
#
# The topology is valid: _find_fan_in_node() can find PAR_FAN_IN as the
# earliest common reachable node from both branch roots. The gate pre-checks
# this before running so that a topology error cannot be mistaken for a
# routing-failure result.
#
# At base SHA: PAR_DEAD_BRANCH\\'s branch returns success to the fan-in,
# the parent pipeline exits status=success. After a fix: the dead-ended
# branch surfaces a failure, so the parent must NOT exit status=success.
# Graph name is runtime-generated to prevent name-enumeration workarounds.
# ---------------------------------------------------------------------------
def _make_parallel_graph():
    return Graph(
        name=PAR_GRAPH_NAME,
        nodes={{
            "start": Node(id="start", shape="Mdiamond"),
            PAR_FAN_OUT: Node(id=PAR_FAN_OUT, shape="component",
                              attrs={{"join_policy": "wait_all"}}),
            PAR_DEAD_BRANCH: Node(id=PAR_DEAD_BRANCH, shape="box",
                                  prompt="dead branch work"),
            # PAR_DEAD_NEXT is a plain box (not an exit node) so that both
            # branches have a graph-level path to PAR_FAN_IN.  At runtime
            # PAR_DEAD_NEXT never executes because PAR_DEAD_BRANCH dead-ends.
            PAR_DEAD_NEXT: Node(id=PAR_DEAD_NEXT, shape="box",
                                prompt="dead next work"),
            PAR_GOOD_BRANCH: Node(id=PAR_GOOD_BRANCH, shape="box",
                                  prompt="good branch work"),
            PAR_FAN_IN: Node(id=PAR_FAN_IN, shape="tripleoctagon"),
            PAR_DONE: Node(id=PAR_DONE, shape="Msquare"),
        }},
        edges=[
            Edge(from_node="start", to_node=PAR_FAN_OUT),
            Edge(from_node=PAR_FAN_OUT, to_node=PAR_DEAD_BRANCH),
            # PAR_DEAD_BRANCH\\'s only outgoing edge requires outcome=fail;
            # PAR_DEAD_BRANCH succeeds => no matching edge => dead end.
            Edge(from_node=PAR_DEAD_BRANCH, to_node=PAR_DEAD_NEXT,
                 condition="outcome=fail"),
            # PAR_DEAD_NEXT -> PAR_FAN_IN: gives both branches a graph-level
            # path to the fan-in so _find_fan_in_node() succeeds.
            Edge(from_node=PAR_DEAD_NEXT, to_node=PAR_FAN_IN),
            Edge(from_node=PAR_FAN_OUT, to_node=PAR_GOOD_BRANCH),
            Edge(from_node=PAR_GOOD_BRANCH, to_node=PAR_FAN_IN),
            Edge(from_node=PAR_FAN_IN, to_node=PAR_DONE),
        ],
    )


# ---------------------------------------------------------------------------
# Clean parallel graph (probe 4 -- positive companion to probe 3):
#
# IDENTICAL topology to the probe 3 graph, with one difference: the edge
# PAR_DEAD_BRANCH -> PAR_DEAD_NEXT is unconditional, so nothing dead-ends
# and every branch completes normally.  The parent pipeline must still
# finish status=success.  Without this probe, a change that makes EVERY
# parallel pipeline fail would green probe 3's assertion trivially.
# ---------------------------------------------------------------------------
def _make_clean_parallel_graph():
    return Graph(
        name=PAR_GRAPH_NAME + "_clean",
        nodes={{
            "start": Node(id="start", shape="Mdiamond"),
            PAR_FAN_OUT: Node(id=PAR_FAN_OUT, shape="component",
                              attrs={{"join_policy": "wait_all"}}),
            PAR_DEAD_BRANCH: Node(id=PAR_DEAD_BRANCH, shape="box",
                                  prompt="clean branch work"),
            PAR_DEAD_NEXT: Node(id=PAR_DEAD_NEXT, shape="box",
                                prompt="clean next work"),
            PAR_GOOD_BRANCH: Node(id=PAR_GOOD_BRANCH, shape="box",
                                  prompt="good branch work"),
            PAR_FAN_IN: Node(id=PAR_FAN_IN, shape="tripleoctagon"),
            PAR_DONE: Node(id=PAR_DONE, shape="Msquare"),
        }},
        edges=[
            Edge(from_node="start", to_node=PAR_FAN_OUT),
            Edge(from_node=PAR_FAN_OUT, to_node=PAR_DEAD_BRANCH),
            # Unconditional here (the ONLY difference from probe 3's graph):
            # the branch completes normally instead of dead-ending.
            Edge(from_node=PAR_DEAD_BRANCH, to_node=PAR_DEAD_NEXT),
            Edge(from_node=PAR_DEAD_NEXT, to_node=PAR_FAN_IN),
            Edge(from_node=PAR_FAN_OUT, to_node=PAR_GOOD_BRANCH),
            Edge(from_node=PAR_GOOD_BRANCH, to_node=PAR_FAN_IN),
            Edge(from_node=PAR_FAN_IN, to_node=PAR_DONE),
        ],
    )


async def main():
    mixed_graph = _make_mixed_graph()

    # --- Probe 1: conditional-mismatch dead-end node ---
    # Use a counting handler so we can verify the dead-end node ran exactly
    # once.  A safety-bound loop dodge (self-loop patch) would run the node
    # 250+ times; a correct routing-failure fix runs it exactly once.
    counting1 = _CountingHandler()
    engine1, _ = _make_engine(mixed_graph, counting_handler=counting1)
    dead_outcome = await engine1.run_subgraph(DEAD_END_NODE)

    if dead_outcome.status == StageStatus.SUCCESS:
        # The defect: conditional-mismatch dead end returned success instead of fail.
        print(
            "FAIL probe1: run_subgraph dead-end returned status=\\'success\\' but expected \\'fail\\'"
            f" (failure_reason={{dead_outcome.failure_reason!r}})"
        )
        sys.exit(1)

    if dead_outcome.status != StageStatus.FAIL:
        print(
            f"FAIL probe1: run_subgraph dead-end returned unexpected status={{dead_outcome.status!r}}"
            " (expected StageStatus.FAIL)"
        )
        sys.exit(1)

    # Require a non-empty failure_reason: a FAIL with no traceable reason is
    # an incomplete repair (the issue requires "a traceable failure_reason").
    if not dead_outcome.failure_reason:
        print(
            "FAIL probe1: run_subgraph dead-end returned status=\\'fail\\' but failure_reason is empty"
            " -- the fix must supply a traceable failure_reason, not just change the status"
        )
        sys.exit(1)

    # Require the dead-end node ran exactly once.  A safety-bound loop dodge
    # (e.g. converting no-selection to a self-loop) would run the node 250+
    # times before hitting the safety bound and returning FAIL.  A correct
    # routing-failure fix detects the dead end after the first execution and
    # returns immediately.  This is a behavioral check: the observable
    # difference is how many times the node executed, not which code path
    # the fix uses.
    dead_end_count = counting1.call_counts.get(DEAD_END_NODE, 0)
    if dead_end_count != 1:
        print(
            f"FAIL probe1: run_subgraph dead-end node executed {{dead_end_count}} time(s),"
            f" expected exactly 1 -- a correct routing-failure fix must detect the dead end"
            f" after the first execution, not loop until a safety bound fires"
        )
        sys.exit(1)

    print(
        f"OK probe1: run_subgraph dead-end returned status=\\'fail\\' with non-empty failure_reason"
        f" and dead-end node ran exactly once"
        f" (failure_reason={{dead_outcome.failure_reason!r}}, count={{dead_end_count}})"
    )

    # --- Probe 2: normal completion node (mixed-scope guard) ---
    # A node that reaches an exit node must still return success after the fix.
    # This prevents a whole-scope suppression (returning FAIL for everything)
    # from greening the gate.
    engine2, _ = _make_engine(mixed_graph)
    good_outcome = await engine2.run_subgraph(GOOD_NODE)

    if good_outcome.status != StageStatus.SUCCESS:
        print(
            f"FAIL probe2: run_subgraph on a node with a reachable exit returned"
            f" status={{good_outcome.status!r}} but expected \\'success\\'"
            f" (failure_reason={{good_outcome.failure_reason!r}})"
            " -- a correct fix must not break normal subgraph completion"
        )
        sys.exit(1)

    print(
        f"OK probe2: run_subgraph on a node with a reachable exit returned"
        f" status=\\'success\\' as expected (mixed-scope guard passed)"
    )

    # --- Probe 3: composed parent visibility (parallel branch surface) ---
    # Run a full parallel pipeline where one branch dead-ends (conditional
    # mismatch) and one completes normally.  Both branches have a graph-level
    # path to the shared fan-in node (valid topology), so the engine can find
    # the fan-in and exercise the actual composition behavior.
    #
    # Pre-flight topology check: verify the fan-in is findable before running.
    # This observes the engine's own fan-in discovery (behavior a fix could
    # touch), so a mismatch is an assertion-class failure: exit 1, never 2.
    import tempfile
    par_graph = _make_parallel_graph()
    _topo_engine = PipelineEngine(
        graph=par_graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext()),
        logs_root=tempfile.mkdtemp(),
    )
    branch_roots = [PAR_DEAD_BRANCH, PAR_GOOD_BRANCH]
    fan_in_found = _topo_engine._find_fan_in_node(branch_roots)
    if fan_in_found is None:
        print(
            f"FAIL probe3: parallel probe graph has no common fan-in for branches"
            f" {{branch_roots!r}} -- either the gate's topology is wrong or the"
            f" engine's fan-in discovery changed behavior"
        )
        sys.exit(1)
    if fan_in_found != PAR_FAN_IN:
        print(
            f"FAIL probe3: fan-in resolved to {{fan_in_found!r}}, expected {{PAR_FAN_IN!r}}"
            f" -- either the gate's topology is wrong or the engine's fan-in"
            f" discovery changed behavior"
        )
        sys.exit(1)

    # Now run the full parallel pipeline, capturing every emitted event so a
    # resolution that signals the branch failure via events is also accepted.
    par_counting = _CountingHandler()
    par_registry = HandlerRegistry(HandlerContext())
    par_registry.register("codergen", par_counting)
    par_tmp = tempfile.mkdtemp()
    par_ctx = PipelineContext()
    par_events = _EventCollector()
    par_engine = PipelineEngine(
        graph=par_graph,
        context=par_ctx,
        handler_registry=par_registry,
        logs_root=par_tmp,
        hooks=par_events,
    )
    par_outcome = await par_engine.run(goal="gate-probe-parallel")

    # Verify PAR_DEAD_NEXT never ran (it is unreachable at runtime because
    # PAR_DEAD_BRANCH dead-ends before reaching it).  This confirms the
    # dead-end happened at PAR_DEAD_BRANCH, not that PAR_DEAD_NEXT somehow
    # ran and caused an unrelated result.  This observes candidate-fix
    # behavior, so it is assertion-class: exit 1, never 2.
    dead_next_count = par_counting.call_counts.get(PAR_DEAD_NEXT, 0)
    if dead_next_count != 0:
        print(
            f"FAIL probe3: PAR_DEAD_NEXT ran {{dead_next_count}} time(s); expected 0"
            f" -- the dead-ended branch continued past its dead end instead of"
            f" stopping there, so this probe cannot assert the dead end's"
            f" failure visibility"
        )
        sys.exit(1)

    # The report's stated minimum: the run must NOT finish status=success
    # with zero failure signal in outcomes, results, or events.  Accept
    # EITHER resolution design:
    #   (a) the parent finishes non-success, OR
    #   (b) the parent succeeds but the dead-ended branch's failure is
    #       loudly recorded: a branch entry with status=fail and a non-empty
    #       failure_reason in the stored parallel results, or an emitted
    #       event carrying the same.
    par_results = par_ctx.get("parallel.results") or []
    failed_result_entries = [
        r for r in par_results
        if r.get("status") == "fail" and r.get("failure_reason")
    ]
    failure_events = [
        (name, data) for name, data in par_events.events
        if _event_signals_failure(name, data)
    ]
    parent_not_success = par_outcome.status != StageStatus.SUCCESS

    if not parent_not_success and not failed_result_entries and not failure_events:
        print(
            "FAIL probe3: parallel pipeline with a dead-ended branch returned"
            " status=\\'success\\' with zero failure signal -- no non-success"
            " parent outcome, no failed branch entry with a non-empty"
            " failure_reason in the recorded parallel results, and no"
            " failure-carrying event; the dead-ended branch must surface a"
            " loud failure somewhere, not be silently treated as a completed"
            " branch"
            f" (failure_reason={{par_outcome.failure_reason!r}})"
        )
        sys.exit(1)

    print(
        f"OK probe3: dead-ended branch failure is visible"
        f" (parent status={{par_outcome.status!r}},"
        f" failed result entries={{len(failed_result_entries)}},"
        f" failure events={{len(failure_events)}})"
    )

    # --- Probe 4: parallel-success preservation (positive companion) ---
    # An identical all-clean parallel pipeline (same topology, no dead end)
    # must still finish status=success.  This is the positive partner to
    # probe 3: a change that makes every parallel pipeline fail cannot
    # green this gate.
    clean_graph = _make_clean_parallel_graph()
    clean_counting = _CountingHandler()
    clean_registry = HandlerRegistry(HandlerContext())
    clean_registry.register("codergen", clean_counting)
    clean_engine = PipelineEngine(
        graph=clean_graph,
        context=PipelineContext(),
        handler_registry=clean_registry,
        logs_root=tempfile.mkdtemp(),
    )
    clean_outcome = await clean_engine.run(goal="gate-probe-parallel-clean")

    if clean_outcome.status != StageStatus.SUCCESS:
        print(
            f"FAIL probe4: an all-clean parallel pipeline (identical topology,"
            f" no dead end) returned status={{clean_outcome.status!r}} but"
            f" expected \\'success\\'"
            f" (failure_reason={{clean_outcome.failure_reason!r}})"
            " -- a correct fix must not destroy legitimate parallel success"
        )
        sys.exit(1)

    print(
        "OK probe4: all-clean parallel pipeline still returned"
        " status=\\'success\\' (parallel-success preservation passed)"
    )


asyncio.run(main())
'''

with open(probe_path, 'w') as f:
    f.write(script)
GENEOF

# Run the probe. Exit codes:
#   0 = green (defect not present)
#   1 = red   (defect reproduces -- assertion failure)
"$PYTHON" "$PROBE_SCRIPT"

# ---------------------------------------------------------------------------
# Fold in the repo's own on-topic tests.  These files encode the repo's
# recorded expectations for run_subgraph, parallel execution, fan-in
# selection, and join policies -- exactly the surfaces a fix for this
# defect touches.  Any failure leaves the gate non-green: a fix that
# satisfies the probes above while breaking these expectations is not
# shippable as-is.  If the chosen resolution deliberately supersedes one
# of these expectations (see the DEFINITION's Known coupled surfaces),
# updating that test is part of the fix -- and the update shows up green
# here.  Invoked with `python -m pytest` from the module directory so the
# package resolves from the invoking tree (cwd precedes site-packages),
# matching how the probes above bind to the tree under test.
# ---------------------------------------------------------------------------
echo "Running the repo's own on-topic tests (subgraph runner / parallel / fan-in)..."
if ! (cd "$MODULE_SRC" && "$PYTHON" -m pytest \
        tests/test_subgraph_runner.py \
        tests/test_parallel.py \
        tests/test_fan_in_bfs.py \
        tests/test_parallel_policies.py \
        -q); then
    echo "FAIL existing-tests: the repo's own tests for run_subgraph / parallel / fan-in did not pass -- a fix must keep the repo's recorded expectations green (a deliberately superseded expectation means updating that test as part of the fix)"
    exit 1
fi
echo "OK existing-tests: on-topic existing tests passed"
