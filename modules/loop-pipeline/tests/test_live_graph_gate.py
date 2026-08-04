"""Live-graph smoke test -- the automated mechanism for AGENTS.md's live-run gate.

WHY THIS FILE EXISTS
--------------------
AGENTS.md ("Test commands" / "Verification gradient") requires that any
change to `engine.py` or handler dispatch logic be checked with a live
pipeline run on a real graph, not unit tests alone:

    "Unit tests alone are insufficient for engine and handler changes. Past
    bugs have shipped with green unit tests and failed on first real-graph
    run, specifically at the boundary between the engine's main loop and
    handler dispatch."

Until this file existed, that requirement was enforced by asking a human
reviewer to notice a missing live-run paste in a PR description -- a
reminder, not a mechanism, and reminders decay. This module is the
mechanism: a permanent, CI-run, hermetic instance of that live-graph check
so the gate cannot silently lapse again. It does not replace the human
judgment call for changes it doesn't cover (see the updated AGENTS.md
verification-gradient table) -- it establishes a baseline that always runs.

WHAT MAKES THIS A "LIVE" RUN AND NOT A UNIT TEST IN DISGUISE
--------------------------------------------------------------
Every test below drives real DOT graph text through the REAL pipeline:

    DOT text --(real parse_dot())--> Graph
             --(real PipelineEngine)--> main loop
             --(real HandlerRegistry)--> real handler dispatch

Nothing here stubs the engine, hand-builds an Outcome/event list, or
bypasses handler dispatch. The only deterministic substitutions (required
for hermetic, network-free, API-key-free CI execution) are:

  - real shell commands (``echo ...``) for tool nodes, executed through the
    real, unmodified ``ToolHandler`` / subprocess dispatch;
  - ``StatefulRetryBackend``, a real implementation of the documented
    ``CodergenBackend`` protocol seam (see ``CodergenHandler.__init__``'s
    own docstring, and every existing test in this suite that passes
    ``backend=MockBackend()``) in place of an actual LLM call. This is the
    officially sanctioned test seam, not an engine bypass.

Assertions are made against the actual event stream emitted by the engine's
own ``await self.hooks.emit(event_name, data)`` calls, and cross-checked
against ``engine.node_outcomes`` -- never against a hand-built expectation
of what the engine "should" do.

PROVENANCE
----------
Adapted from the harness used to produce the live-run evidence for the
epic#371 observability-trio PR (historical verification artifact, not
re-derived from memory). Five claims are covered, each pinned to a specific,
previously-regressed behavior at the engine/handler-dispatch boundary:

  1. A ``shape=component`` parallel fan-out emits each branch node exactly
     once (not twice) as ``pipeline:node_start`` / ``pipeline:node_complete``,
     each tagged ``via_parallel=True``.
  2. A retrying node's real attempt count (consumed by the real retry
     ladder in ``retry.py``) survives the ``auto_status`` SKIPPED->SUCCESS
     override.
  3. A retrying node's real attempt count AND its structured
     ``failed_step`` diagnostic both survive the ``continue_on_fail``
     FAIL->SUCCESS override, simultaneously, on the same event.
  4. A manager-loop child engine's events (a second, independently
     constructed ``PipelineEngine`` instance) reach the PARENT hooks event
     stream, rather than being silently dropped.
  5. A bare, unquoted ``continue_on_fail=true`` (coerced to Python ``bool``
     by the DOT parser, not the string ``"true"``) still overrides a FAIL
     outcome to SUCCESS end-to-end. Regression coverage for the
     resolve_bool_attr() consolidation (issue #389): prior to that fix,
     the engine's strict ``== "true"`` string comparison silently never
     matched a bare/unquoted boolean attribute.
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus

# ---------------------------------------------------------------------------
# Real DOT graph fixtures (inlined -- no filesystem fixture resolution needed
# except for claim 4, which by design requires a real child .dot file on
# disk so the real `resolve_dot_path()` / `stack.child_dotfile` machinery is
# exercised, not bypassed).
# ---------------------------------------------------------------------------

CLAIM1_PARALLEL_DOT = """
// Proves parallel branch events are not double-counted.
// shape=component fans out to 3 real tool-node branches (parallelogram,
// each running a real deterministic shell command). All branches converge
// on the tripleoctagon fan-in node. Each branch node must appear EXACTLY
// ONCE as pipeline:node_start / pipeline:node_complete with via_parallel=True.
digraph Claim1Parallel {
    graph [goal="Prove parallel branch events are not double-counted"]
    rankdir=TB

    start   [shape=Mdiamond, label="Start"]
    fanout  [shape=component, label="Fan Out", max_parallel=3, join_policy="wait_all"]

    branch_a [shape=parallelogram, label="Branch A", tool_command="echo branch_a_ran"]
    branch_b [shape=parallelogram, label="Branch B", tool_command="echo branch_b_ran"]
    branch_c [shape=parallelogram, label="Branch C", tool_command="echo branch_c_ran"]

    joinnode [shape=tripleoctagon, label="Join"]
    exit     [shape=Msquare, label="Exit"]

    start -> fanout
    fanout -> branch_a
    fanout -> branch_b
    fanout -> branch_c
    branch_a -> joinnode
    branch_b -> joinnode
    branch_c -> joinnode
    joinnode -> exit
}
"""

CLAIM2_AUTO_STATUS_DOT = """
// Proves attempt_count survives auto_status promotion.
// auto_node has auto_status=true and max_retries=2 (3 max attempts). The
// deterministic backend (StatefulRetryBackend, real CodergenBackend
// protocol implementation -- no LLM/network call) returns RETRY on
// attempts 1-2 and a no-status (SKIPPED) result on attempt 3. auto_status
// then promotes that SKIPPED outcome to SUCCESS. The real attempt count
// (3) consumed by the real retry ladder must survive the promotion and
// appear on the emitted pipeline:node_complete event.
digraph Claim2AutoStatus {
    graph [goal="Prove attempt_count survives auto_status promotion"]
    rankdir=TB

    start     [shape=Mdiamond, label="Start"]
    auto_node [shape=box, label="Auto Node", prompt="do work",
               auto_status=true, max_retries=2]
    exit      [shape=Msquare, label="Exit"]

    start -> auto_node -> exit
}
"""

CLAIM3_CONTINUE_ON_FAIL_DOT = """
// Proves attempt_count AND failed_step survive continue_on_fail.
// fail_node has continue_on_fail=true (bare, unquoted -- the DOT parser
// coerces this to Python bool True; see resolve_bool_attr() in graph.py,
// issue #389) and max_retries=2 (3 max attempts). The deterministic
// backend returns RETRY on attempts 1-2 and a genuine FAIL with a
// structured failed_step payload on attempt 3. continue_on_fail then
// overrides FAIL->SUCCESS for routing, but must NOT erase the real
// attempt count (3) or the failed_step diagnostic.
digraph Claim3ContinueOnFail {
    graph [goal="Prove attempt_count and failed_step survive continue_on_fail"]
    rankdir=TB

    start     [shape=Mdiamond, label="Start"]
    fail_node [shape=box, label="Fail Node", prompt="do work",
               continue_on_fail=true, max_retries=2]
    exit      [shape=Msquare, label="Exit"]

    start -> fail_node -> exit
}
"""

CLAIM5_CONTINUE_ON_FAIL_UNQUOTED_DOT = """
// Proves a bare, unquoted continue_on_fail=true overrides FAIL->SUCCESS
// end-to-end (issue #389 regression coverage). continue_on_fail=true
// (unquoted) is coerced by the DOT parser to Python bool True, not the
// string "true". Prior to the resolve_bool_attr() consolidation, the
// engine's fail-closed comparison was `== "true"` (a strict string
// compare), so this exact bare form silently never overrode the failure --
// the node's FAIL would have surfaced unchanged and the pipeline would
// have failed overall. single_attempt_node has no max_retries, so this is
// a minimal, single-call reproduction of the reported bug (distinct from
// Claim 3's multi-retry attempt_count/failed_step scenario above).
digraph Claim5ContinueOnFailUnquoted {
    graph [goal="Prove unquoted continue_on_fail=true overrides FAIL to SUCCESS"]
    rankdir=TB

    start               [shape=Mdiamond, label="Start"]
    single_attempt_node [shape=box, label="Single Attempt Node", prompt="do work",
                         continue_on_fail=true]
    exit                [shape=Msquare, label="Exit"]

    start -> single_attempt_node -> exit
}
"""

CLAIM4_MANAGER_CHILD_DOT = """
// Child pipeline for the manager-loop parent graph below. child_task is a
// real tool node (parallelogram) executing a real, deterministic shell
// command. If manager-loop hooks wiring works, this child engine's own
// pipeline:start and node events must appear in the PARENT engine's event
// stream (tagged via a branch_id scope discriminator).
digraph Claim4ManagerChild {
    graph [goal="Run one deterministic tool step as the manager's child"]
    rankdir=TB

    child_start [shape=Mdiamond, label="Child Start"]
    child_task  [shape=parallelogram, label="Child Task",
                 tool_command="echo child_task_ran"]
    child_done  [shape=Msquare, label="Child Done"]

    child_start -> child_task -> child_done
}
"""

CLAIM4_MANAGER_PARENT_DOT = """
// Proves the manager-loop child engine is observable: child-node events
// must reach the PARENT hooks event stream. manager (shape=house)
// supervises a child pipeline loaded from claim4_manager_child.dot via
// stack.child_dotfile. The child pipeline contains a real tool node
// running a real deterministic shell command. manager.max_cycles=1 keeps
// the run deterministic.
digraph Claim4ManagerParent {
    graph [goal="Prove manager-loop child engine events reach the parent stream"]
    rankdir=TB

    start [shape=Mdiamond, label="Start"]
    done  [shape=Msquare,  label="Done"]

    manager [
        shape=house,
        label="Supervise Child",
        prompt="Oversee the child pipeline.",
        manager.max_cycles=1,
        manager.actions=observe,
        manager.stop_condition="outcome=success",
        stack.child_dotfile="claim4_manager_child.dot"
    ]

    start -> manager -> done
}
"""


# ---------------------------------------------------------------------------
# Real-protocol test seams (see module docstring: not engine/handler bypass)
# ---------------------------------------------------------------------------


class RecordingHooks:
    """Real hooks object: records every engine-emitted event verbatim, in
    memory. This is the "attractor-compatible resolver" stand-in AGENTS.md
    permits for a live run -- it does not intercept or alter engine
    behavior, it only records what the real engine's
    ``await self.hooks.emit(event_name, data)`` calls send it.
    """

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, event_name: str, data: dict) -> None:
        self.events.append({"event": event_name, "data": data})

    def of_type(self, event_name: str) -> list[dict]:
        return [e for e in self.events if e["event"] == event_name]


class StatefulRetryBackend:
    """Real ``CodergenBackend``-protocol implementation -- NOT a fake handler.

    ``CodergenHandler`` (the real, unmodified production handler for
    ``shape=box`` nodes) is still the code executing; only the pluggable
    ``backend`` it calls (the documented LLM-call seam) is deterministic.
    Returns ``Outcome(status=RETRY)`` for the first ``retries_before_final``
    calls (driving the real retry ladder through real attempt bookkeeping),
    then returns whatever ``final_outcome_factory()`` produces on the final
    call.
    """

    def __init__(self, retries_before_final: int, final_outcome_factory) -> None:
        self.retries_before_final = retries_before_final
        self.final_outcome_factory = final_outcome_factory
        self.calls = 0

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls += 1
        if self.calls <= self.retries_before_final:
            return Outcome(status=StageStatus.RETRY)
        return self.final_outcome_factory()


async def _run_graph(
    dot_source: str,
    logs_root: str,
    backend=None,
    source_dir: str | None = None,
):
    """Parse real DOT text and run it end-to-end through the real engine."""
    graph = parse_dot(dot_source)  # REAL parser
    if source_dir is not None:
        graph.source_dir = source_dir

    hooks = RecordingHooks()
    context = PipelineContext()
    ctx = HandlerContext(backend=backend, hooks=hooks, cancel_event=None)
    registry = HandlerRegistry(ctx)  # REAL handler registry, all real handlers
    engine = PipelineEngine(  # REAL engine
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
        hooks=hooks,
    )

    outcome = await engine.run()  # REAL main loop, real dispatch
    return outcome, engine, hooks


# ---------------------------------------------------------------------------
# Claim 1: parallel fan-out, no double-counted branch events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_fan_out_emits_each_branch_exactly_once(tmp_path):
    outcome, _engine, hooks = await _run_graph(
        CLAIM1_PARALLEL_DOT, str(tmp_path / "claim1")
    )

    assert outcome.status == StageStatus.SUCCESS

    starts = hooks.of_type("pipeline:node_start")
    completes = hooks.of_type("pipeline:node_complete")

    for branch_id in ("branch_a", "branch_b", "branch_c"):
        branch_starts = [e for e in starts if e["data"]["node_id"] == branch_id]
        branch_completes = [e for e in completes if e["data"]["node_id"] == branch_id]

        # Exactly one start and one complete per branch -- not two (the
        # historical Bug G regression double-dispatched each branch: once
        # inside ParallelHandler's subgraph runner, once again via the
        # engine's own multi-edge fan-out).
        assert len(branch_starts) == 1, (
            f"{branch_id} node_start count != 1: {branch_starts}"
        )
        assert len(branch_completes) == 1, (
            f"{branch_id} node_complete count != 1: {branch_completes}"
        )

        # Both must be tagged via_parallel=True -- the documented contract
        # downstream observability relies on (see AGENTS.md "Common
        # pitfalls" -- per-branch event contract).
        assert branch_starts[0]["data"].get("via_parallel") is True
        assert branch_completes[0]["data"].get("via_parallel") is True


# ---------------------------------------------------------------------------
# Claim 2: attempt_count survives auto_status promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attempt_count_survives_auto_status_promotion(tmp_path):
    backend = StatefulRetryBackend(
        retries_before_final=2,
        final_outcome_factory=lambda: Outcome(status=StageStatus.SKIPPED),
    )

    outcome, engine, hooks = await _run_graph(
        CLAIM2_AUTO_STATUS_DOT, str(tmp_path / "claim2"), backend=backend
    )

    assert outcome.status == StageStatus.SUCCESS
    # 3 real calls consumed by the real retry ladder (2 retries + 1 final).
    assert backend.calls == 3

    node_outcome = engine.node_outcomes["auto_node"]
    assert node_outcome.status == StageStatus.SUCCESS
    assert node_outcome.attempt_count == 3

    completes = [
        e
        for e in hooks.of_type("pipeline:node_complete")
        if e["data"]["node_id"] == "auto_node"
    ]
    # Exactly one node_complete for auto_node, reporting the real attempt
    # count rather than the attempt_count=None fallback of 1.
    assert len(completes) == 1
    assert completes[0]["data"].get("attempt") == 3


# ---------------------------------------------------------------------------
# Claim 3: attempt_count AND failed_step survive continue_on_fail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attempt_count_and_failed_step_survive_continue_on_fail(tmp_path):
    expected_failed_step = {
        "command": "false",
        "exit_code": 1,
        "duration_s": 0.01,
        "stdout_tail": "",
        "stderr_tail": "simulated deterministic tool failure (live-graph gate)",
    }

    def _final():
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason="tool exited non-zero (simulated, deterministic)",
            failed_step=dict(expected_failed_step),
        )

    backend = StatefulRetryBackend(retries_before_final=2, final_outcome_factory=_final)

    outcome, engine, hooks = await _run_graph(
        CLAIM3_CONTINUE_ON_FAIL_DOT, str(tmp_path / "claim3"), backend=backend
    )

    # continue_on_fail overrides FAIL -> SUCCESS for routing; the pipeline
    # completes overall (routing proceeds past fail_node to exit normally).
    assert outcome.status == StageStatus.SUCCESS
    assert backend.calls == 3

    node_outcome = engine.node_outcomes["fail_node"]
    assert node_outcome.attempt_count == 3
    assert node_outcome.failed_step == expected_failed_step

    completes = [
        e
        for e in hooks.of_type("pipeline:node_complete")
        if e["data"]["node_id"] == "fail_node"
    ]
    # Exactly one node_complete for fail_node: status success (override
    # took effect), attempt 3 (survived), and the full failed_step dict
    # intact (survived) -- all simultaneously, on the same event.
    assert len(completes) == 1
    data = completes[0]["data"]
    assert data.get("status") == "success"
    assert data.get("attempt") == 3
    assert data.get("failed_step") == expected_failed_step


# ---------------------------------------------------------------------------
# Claim 4: manager-loop child engine is observable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_loop_child_engine_events_reach_parent_stream(tmp_path):
    # This claim exercises stack.child_dotfile resolution
    # (handlers/pipeline.py's resolve_dot_path), which reads the child DOT
    # file from disk relative to graph.source_dir -- so, unlike the other
    # three claims, this one needs a real file on disk rather than only an
    # in-memory DOT string.
    source_dir = tmp_path / "claim4"
    source_dir.mkdir()
    (source_dir / "claim4_manager_child.dot").write_text(
        CLAIM4_MANAGER_CHILD_DOT, encoding="utf-8"
    )

    outcome, _engine, hooks = await _run_graph(
        CLAIM4_MANAGER_PARENT_DOT,
        str(source_dir / "logs"),
        source_dir=str(source_dir),
    )

    assert outcome.status == StageStatus.SUCCESS

    # The child engine is a second, independently constructed
    # PipelineEngine instance (built inside
    # ManagerLoopHandler._run_child_dotfile()). Before hooks=/cancel_event=
    # were wired into that construction, the child engine had hooks=None
    # and this entire execution class was invisible in the parent's event
    # stream -- only the manager node's own final node_complete was
    # visible. Assert the child's own node events (not just the manager
    # node's outcome) reached the parent stream.
    child_node_ids = {
        e["data"].get("node_id") for e in hooks.of_type("pipeline:node_start")
    }
    assert "child_start" in child_node_ids
    assert "child_task" in child_node_ids

    manager_completes = [
        e
        for e in hooks.of_type("pipeline:node_complete")
        if e["data"]["node_id"] == "manager"
    ]
    assert len(manager_completes) == 1
    assert manager_completes[0]["data"].get("status") == "success"


# ---------------------------------------------------------------------------
# Claim 5: bare, unquoted continue_on_fail=true overrides FAIL to SUCCESS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unquoted_continue_on_fail_overrides_fail_to_success(tmp_path):
    class AlwaysFailBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            return Outcome(status=StageStatus.FAIL, failure_reason="simulated failure")

    outcome, engine, _hooks = await _run_graph(
        CLAIM5_CONTINUE_ON_FAIL_UNQUOTED_DOT,
        str(tmp_path / "claim5"),
        backend=AlwaysFailBackend(),
    )

    # continue_on_fail=true (bare/unquoted, coerced to Python bool by the
    # real parser) must override FAIL -> SUCCESS just like the quoted form
    # in Claim 3 -- this is the exact scenario reported in issue #389.
    assert engine.node_outcomes["single_attempt_node"].status == StageStatus.SUCCESS, (
        f"Expected single_attempt_node outcome to be SUCCESS after unquoted "
        f"continue_on_fail=true override, got "
        f"{engine.node_outcomes['single_attempt_node'].status!r}"
    )
    assert outcome.status == StageStatus.SUCCESS, (
        f"Expected overall pipeline SUCCESS, got {outcome.status!r}"
    )
