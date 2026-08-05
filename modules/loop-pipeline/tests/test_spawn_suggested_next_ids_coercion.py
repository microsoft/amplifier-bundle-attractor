"""End-to-end spawn-path test: suggested_next_ids type coercion.

Closes a gap the multi-turn convergence test explicitly excludes:
``test_report_outcome_multiturn_convergence.py`` documents itself as
"Path B, direct tool loop -- no session.spawn". Nothing in the suite drove
a ``goal_gate=true`` node's outcome through the ACTUAL ``session.spawn``
boundary (``AmplifierBackend._run_via_spawn`` -> ``_parse_outcome``) and
then through real ``edge_selection.select_edge()`` routing.

The bug this closes (pre-existing, independent of PR #133): edge_selection's
Step 3 ("Suggested next IDs") compared ``e.to_node == suggested_id`` with no
type coercion. Node IDs are strings everywhere; a spawned child that emits a
JSON verdict with a bare numeric ID (e.g. ``{"suggested_next_ids": [2]}``
instead of ``{"suggested_next_ids": ["2"]}``) could never match, because
``"2" == 2`` is always False in Python.

This reaches the pipeline via the spawn path today through
``AmplifierBackend._parse_outcome()`` (invoked from ``_run_via_spawn`` on any
non-empty child final message that carries a JSON or embedded verdict) --
this parsing path performs NO per-element type validation on
``suggested_next_ids`` before constructing the ``Outcome``, on main as of
this branch's base commit. Two adversarial scenarios are exercised, matching
the two ways the bug manifests in a real graph:

  1. WITH an unconditional fallback edge present: the type mismatch defeats
     Step 3, so routing falls through to Step 4 (weight/lexical tiebreak)
     and silently lands on the WRONG node -- no error, no trace.
  2. WITHOUT one (only edges ineligible for the fallback ladder): the type
     mismatch defeats Step 3, Step 4 finds nothing either, and the pipeline
     hard-fails with ``no_matching_edge`` -- a real failure this time, but
     the assertion here is for the DIAGNOSTIC requirement: the resulting
     failure must name what was suggested and what edges existed.

Both fixtures build their adversarial payload via a real JSON round-trip
(``json.dumps`` then the engine's own ``json.loads``) so the int type is
exactly what a real LLM response would produce -- not a hand-typed Python
list that happens to already have the "wrong" type.
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Provide a minimal amplifier_core / amplifier_foundation stub so the
# backend's lazy imports work in the test environment, exactly as
# test_backend.py does for the same reason (conftest.py already does this
# too, but the guard makes this file independently runnable/order-safe).
# ---------------------------------------------------------------------------
if "amplifier_core" not in sys.modules:

    @dataclass
    class _StubMessage:
        role: str = "user"
        content: Any = ""
        tool_call_id: str | None = None
        name: str | None = None
        metadata: dict | None = None

    @dataclass
    class _StubChatRequest:
        messages: list = field(default_factory=list)
        tools: list | None = None
        tool_choice: str | None = None
        reasoning_effort: str | None = None

    _stub_core = types.ModuleType("amplifier_core")
    _stub_core.Message = _StubMessage  # type: ignore[attr-defined]
    _stub_core.ChatRequest = _StubChatRequest  # type: ignore[attr-defined]
    sys.modules["amplifier_core"] = _stub_core

if "amplifier_foundation" not in sys.modules:

    @dataclass
    class _StubProviderPreference:
        provider: str = ""
        model: str = ""

    _stub_foundation = types.ModuleType("amplifier_foundation")
    _stub_foundation.ProviderPreference = _StubProviderPreference  # type: ignore[attr-defined]
    sys.modules["amplifier_foundation"] = _stub_foundation

from amplifier_module_loop_pipeline.backend import AmplifierBackend
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import StageStatus


class _MockSession:
    """Minimal stand-in for AmplifierSession."""

    config: dict[str, Any] = {}


class _SequencedSpawnCoordinator:
    """Coordinator whose session.spawn returns a DIFFERENT result per call.

    Call 1 (the ``assess`` node) returns the adversarial payload under test.
    Every subsequent call (whatever downstream node actually gets executed --
    the correct one OR the wrong one, depending on whether the bug fires)
    returns a plain, uninteresting success so the pipeline can run to
    completion and we can inspect exactly which path it took.
    """

    def __init__(self, first_result: dict, later_result: dict | None = None):
        self._results = [first_result]
        self._later = later_result or {
            "output": json.dumps({"status": "success"}),
            "session_id": "child-later",
        }
        self.spawn_call_count = 0
        self.session = _MockSession()
        self.config: dict[str, Any] = {
            "agents": {
                "attractor-anthropic": {
                    "session": {"orchestrator": {"module": "loop-agent"}},
                },
            }
        }

    def get_capability(self, name: str):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs):
        self.spawn_call_count += 1
        if self.spawn_call_count <= len(self._results):
            return self._results[self.spawn_call_count - 1]
        return self._later


def _make_backend(coordinator: _SequencedSpawnCoordinator) -> AmplifierBackend:
    return AmplifierBackend(
        coordinator=coordinator,
        profiles={"anthropic": "attractor-anthropic"},
    )


def _make_engine(
    graph: Graph, backend: AmplifierBackend, logs_root: str
) -> PipelineEngine:
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
    )


# ---------------------------------------------------------------------------
# Shape A: WITH an unconditional fallback edge -- proves no silent mis-route.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_int_suggested_next_id_does_not_silently_misroute(tmp_path):
    """Adversarial int suggested_next_ids, WITH a competing fallback edge.

    ``assess`` is goal_gate=true and its outcome travels the real spawn path
    (AmplifierBackend._run_via_spawn -> _parse_outcome), exactly the gap the
    existing multi-turn convergence test (Path B, direct tool loop) does not
    cover. The child's JSON verdict names ``suggested_next_ids: [2]`` (a bare
    int, round-tripped through real JSON) intending node "2". A competing
    unconditional edge to "fallback" carries a HIGHER weight, so if Step 3's
    type-mismatched comparison silently fails, Step 4's weight tiebreak lands
    on "fallback" instead -- a silent mis-route with no error at all.

    Without the fix: engine visits "fallback", not "2" -- this assertion
    fails (that is the RED state proving the bug).
    With the fix: engine visits "2" -- the goal_gate node's outcome is also
    correctly treated as an explicit, successful verdict that traveled the
    spawn boundary, so the pipeline completes successfully end to end.
    """
    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "assess": Node(
                id="assess",
                prompt="assess",
                attrs={"llm_provider": "anthropic", "goal_gate": True},
            ),
            "2": Node(id="2", prompt="the correct target"),
            "fallback": Node(id="fallback", prompt="the wrong target"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="assess"),
            # Higher weight: wins Step 4's tiebreak if Step 3 fails to match.
            Edge(from_node="assess", to_node="fallback", weight=5),
            Edge(from_node="assess", to_node="2", weight=1),
            Edge(from_node="2", to_node="exit"),
            Edge(from_node="fallback", to_node="exit"),
        ],
    )

    # Real JSON round-trip: the child's final message is a JSON string; the
    # engine's own json.loads() (inside _parse_outcome) is what produces the
    # Python int 2 in suggested_next_ids -- not a hand-typed fixture.
    adversarial_payload = json.dumps(
        {
            "status": "success",
            "suggested_next_ids": [2],
            "notes": "converged, proceed to node 2",
        }
    )
    round_tripped = json.loads(adversarial_payload)
    assert isinstance(round_tripped["suggested_next_ids"][0], int), (
        "fixture sanity check: the round-tripped id must be a real int, "
        "the same shape a bare-number LLM response produces"
    )

    coordinator = _SequencedSpawnCoordinator(
        first_result={
            "output": adversarial_payload,
            "session_id": "child-assess",
        }
    )
    backend = _make_backend(coordinator)
    engine = _make_engine(graph, backend, str(tmp_path))

    outcome = await engine.run()

    assert coordinator.spawn_call_count >= 1
    assert "2" in engine.completed_nodes, (
        f"expected the suggested node '2' to run; completed={engine.completed_nodes}"
    )
    assert "fallback" not in engine.completed_nodes, (
        "SILENT MIS-ROUTE: suggested_next_ids=[2] (int) was defeated by the "
        "type-mismatched comparison in edge_selection.select_edge Step 3, "
        "and the engine silently fell through to the higher-weight "
        f"'fallback' edge instead. completed={engine.completed_nodes}"
    )
    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"expected the pipeline to complete successfully; got {outcome.status} "
        f"({outcome.failure_reason!r})"
    )


# ---------------------------------------------------------------------------
# Shape B: WITHOUT a usable fallback edge -- proves the failure is loud and
# traceable (names the suggestion and the edges that existed) rather than
# the untraceable "No matching edge from node 'assess'" the engine produces
# today.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_int_suggested_next_id_failure_is_loud_and_traceable(tmp_path):
    """Adversarial int suggested_next_ids, WITHOUT a usable fallback edge.

    ``assess`` (goal_gate=true, spawn path) reports FAIL with
    ``suggested_next_ids: [999]`` -- a bare int that also does not correspond
    to any real node (a hallucinated/adversarial ID, exercising the general
    diagnostic requirement independent of the coercion fix itself). The only
    other outgoing edge is conditional on ``outcome=success`` and will not
    match a FAIL outcome, and its target does not opt in to fail-forward
    routing (``runs_on`` defaults to "success") -- so there is no fallback
    edge Step 4 can use either. The engine must hard-fail with
    ``no_matching_edge``, and the resulting failure_reason/notes must name
    the suggested ID(s) and the edges that actually existed from that node,
    not just the bare, untraceable "No matching edge from node 'assess'".
    """
    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "assess": Node(
                id="assess",
                prompt="assess",
                attrs={"llm_provider": "anthropic", "goal_gate": True},
            ),
            "other": Node(id="other", prompt="only reachable on success"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="assess"),
            # Conditional -- ineligible for Step 3, and (since it won't match
            # a FAIL outcome) ineligible for Step 1 too. Its target also does
            # not opt in to runs_on=always/failure, so Step 4's fail-forward
            # branch has nothing either.
            Edge(from_node="assess", to_node="other", condition="outcome=success"),
            Edge(from_node="other", to_node="exit"),
        ],
    )

    adversarial_payload = json.dumps(
        {
            "status": "fail",
            "suggested_next_ids": [999],
            "failure_reason": "adversarial: no such node",
        }
    )
    round_tripped = json.loads(adversarial_payload)
    assert isinstance(round_tripped["suggested_next_ids"][0], int)

    coordinator = _SequencedSpawnCoordinator(
        first_result={
            "output": adversarial_payload,
            "session_id": "child-assess",
        }
    )
    backend = _make_backend(coordinator)
    engine = _make_engine(graph, backend, str(tmp_path))

    outcome = await engine.run()

    assert outcome.status == StageStatus.FAIL
    combined = f"{outcome.failure_reason or ''} {outcome.notes or ''}"
    assert "assess" in combined, f"failure must name the node: {combined!r}"
    assert "999" in combined, (
        "diagnostic requirement: the failure must name what was suggested "
        f"(suggested_next_ids=[999]) so the real cause is traceable: {combined!r}"
    )
    assert "other" in combined, (
        "diagnostic requirement: the failure must name what edges existed "
        f"from the node so a reader can see the mismatch: {combined!r}"
    )
