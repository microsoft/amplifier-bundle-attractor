"""Every ``tests/e2e/fixtures/*.dot`` must be able to reach its own exit.

Issue #250: ``simple_file_creation.dot`` never routed past ``implement``.  The
filed suspicion was that "a goal-gated node requires labelled/conditional
outgoing edges ... the gate would have nothing to match".  That is not the
mechanism.  The bare ``implement -> done`` edge resolves perfectly well; what
actually happens is a four-step chain that only ends at edge selection:

1. ``implement`` carries ``goal_gate=true``, so EXTENSIONS.md §25's fail-closed
   contract requires an ASSERTED verdict (``report_outcome`` / JSON).
2. The fixture's prompt never asked for one, so a real agent answers in prose.
   ``_parse_outcome`` maps prose on a gated node to ``RETRY``/``is_explicit=
   False`` -- deliberately, so a defaulted response cannot satisfy a gate.
3. The retry budget is spent and the node's recorded outcome becomes ``FAIL``.
4. Only now does routing fail: spec §3.7 fail-fast refuses to traverse an
   UNCONDITIONAL edge on a ``FAIL`` outcome when the target is a default
   ``runs_on=success`` node -- so ``select_edge`` returns ``None`` and the run
   hard-fails with ``"No matching edge from node 'implement'"`` (§33).

The fix is therefore in the prompt, not in the topology: a gated node's prompt
must ask for the verdict its own gate demands.  These tests pin that, through
the real engine rather than by reading the DOT text, so a future edit that
drops the instruction turns the suite red instead of shipping a fixture that
proves nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from amplifier_module_loop_pipeline.backend import _parse_outcome
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.edge_selection import select_edge
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Graph, Node, resolve_bool_attr
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise

_REPO_ROOT = Path(__file__).resolve().parents[3]
_E2E_FIXTURE_DIR = _REPO_ROOT / "tests" / "e2e" / "fixtures"

#: The prose a real agent produces when nothing asked it for a verdict.  This
#: is the shape issue #250 was filed against -- the agent DID the work ("
#: nodes_completed: 2 means node bodies do execute") and then said so in
#: English.
_PROSE_ANSWER = "Done -- I created the file and it prints Hello World."

#: An asserted verdict, in the pure-JSON shape ``_parse_outcome`` recognises as
#: explicit (the same disposition a ``report_outcome`` tool call produces).
_ASSERTED_VERDICT = '{"status": "success", "notes": "work complete"}'

#: A prompt "asks for the verdict" when it names the tool that produces one.
#: The engine's own contract surface is ``report_outcome``; nothing else in a
#: prompt makes an agent assert a status.
_ASKS_FOR_VERDICT_RE = re.compile(r"report_outcome", re.IGNORECASE)


def _e2e_fixture_files() -> list[Path]:
    if not _E2E_FIXTURE_DIR.is_dir():
        return []
    return sorted(_E2E_FIXTURE_DIR.rglob("*.dot"))


_FIXTURES = _e2e_fixture_files()

_needs_fixtures = pytest.mark.skipif(
    not _FIXTURES,
    reason="tests/e2e/fixtures not present (installed-package run)",
)


def _goal_gate_nodes(graph: Graph) -> list[Node]:
    return [
        n
        for n in graph.nodes.values()
        if resolve_bool_attr(n.attrs.get("goal_gate"), "goal_gate")
    ]


class ContractFollowingBackend:
    """An agent that does exactly what each node's own prompt tells it to.

    If the prompt asks for a ``report_outcome`` verdict, it asserts one.  If the
    prompt does NOT ask, it answers in prose -- which is what a real agent does,
    and what issue #250 observed.  Nothing here is stubbed past the backend
    boundary: ``_parse_outcome``, the goal-gate check, the retry ladder and
    ``select_edge`` all run for real, so the assertion below is about routing,
    not about DOT text.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls.append(node.id)
        if _ASKS_FOR_VERDICT_RE.search(prompt or ""):
            return _ASSERTED_VERDICT
        return _PROSE_ANSWER


@_needs_fixtures
@pytest.mark.parametrize(
    "dot_path", _FIXTURES, ids=lambda p: p.name
)
def test_fixture_parses_and_validates(dot_path: Path) -> None:
    """A fixture that cannot even be admitted proves nothing."""
    graph = parse_dot(dot_path.read_text(encoding="utf-8"))
    validate_or_raise(graph)


@_needs_fixtures
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dot_path", _FIXTURES, ids=lambda p: p.name
)
async def test_fixture_reaches_its_exit(dot_path: Path, tmp_path) -> None:
    """Issue #250: every e2e fixture must route to completion, not dead-end.

    Driven by an agent that obeys each node's prompt.  A gated node whose prompt
    forgot to ask for a verdict makes this go red with the exact #250 symptom.
    """
    graph = parse_dot(dot_path.read_text(encoding="utf-8"))
    backend = ContractFollowingBackend()
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()

    detail = (
        f"{dot_path.name}: status={outcome.status.value} "
        f"notes={outcome.notes!r} failure_reason={outcome.failure_reason!r} "
        f"completed={engine.completed_nodes}"
    )
    assert "No matching edge" not in (outcome.notes or ""), detail
    assert "No matching edge" not in (outcome.failure_reason or ""), detail
    assert outcome.is_success, detail


@_needs_fixtures
@pytest.mark.parametrize(
    "dot_path", _FIXTURES, ids=lambda p: p.name
)
def test_goal_gate_prompts_ask_for_the_verdict_they_require(dot_path: Path) -> None:
    """A ``goal_gate=true`` node's prompt must ask for an explicit verdict.

    Grounded in the engine, not in the wording: for each gated node this asserts
    through the REAL ``_parse_outcome`` that (a) prose does not satisfy the gate
    and (b) an asserted verdict does -- so the textual check below is pinned to
    the contract that makes it necessary, rather than standing on its own.
    """
    graph = parse_dot(dot_path.read_text(encoding="utf-8"))
    for node in _goal_gate_nodes(graph):
        prose = _parse_outcome(_PROSE_ANSWER, node=node)
        assert not (prose.is_success and prose.is_explicit), (
            f"{dot_path.name}:{node.id}: prose unexpectedly satisfies the gate "
            f"(status={prose.status.value}, is_explicit={prose.is_explicit}) -- "
            f"EXTENSIONS.md §25 fail-closed contract changed?"
        )
        asserted = _parse_outcome(_ASSERTED_VERDICT, node=node)
        assert asserted.is_success and asserted.is_explicit, (
            f"{dot_path.name}:{node.id}: an asserted verdict does not satisfy "
            f"the gate (status={asserted.status.value}, "
            f"is_explicit={asserted.is_explicit})"
        )
        assert _ASKS_FOR_VERDICT_RE.search(node.prompt or ""), (
            f"{dot_path.name}:{node.id} has goal_gate=true but its prompt never "
            f"asks for a report_outcome verdict. Prose resolves to "
            f"{prose.status.value}/is_explicit={prose.is_explicit}, the retry "
            f"budget is then spent into FAIL, and §3.7 fail-fast refuses the "
            f"node's plain outgoing edge -- the run dead-ends with 'No matching "
            f"edge' (issue #250)."
        )


@_needs_fixtures
@pytest.mark.asyncio
async def test_prose_only_answer_is_what_dead_ended_the_gate(tmp_path) -> None:
    """Characterisation of the #250 mechanism -- the reason the prompt matters.

    This pins the chain the probe found, on a graph shaped exactly like the
    pre-fix fixture, so nobody "fixes" #250 by deleting the verdict instruction
    and papering over the symptom somewhere else.
    """
    dot = """
    digraph mechanism {
        graph [goal="probe"]
        start     [shape=Mdiamond]
        implement [shape=box, prompt="do the work", goal_gate=true]
        done      [shape=Msquare]
        start -> implement -> done
    }
    """
    graph = parse_dot(dot)
    gate = graph.nodes["implement"]

    # Step 2: prose on a gated node is RETRY, never an explicit success.
    prose = _parse_outcome(_PROSE_ANSWER, node=gate)
    assert prose.status is StageStatus.RETRY
    assert prose.is_explicit is False

    # Step 4: the plain edge is fine for every status EXCEPT FAIL.
    ctx = PipelineContext()
    for status in (
        StageStatus.SUCCESS,
        StageStatus.PARTIAL_SUCCESS,
        StageStatus.RETRY,
    ):
        edge = select_edge(
            "implement", Outcome(status=status, is_explicit=True), ctx, graph
        )
        assert edge is not None and edge.to_node == "done", (
            f"the bare implement -> done edge should resolve on {status.value}; "
            f"the filed suspicion that a goal gate needs labelled edges is wrong"
        )
    assert (
        select_edge(
            "implement", Outcome(status=StageStatus.FAIL, is_explicit=True), ctx, graph
        )
        is None
    ), "spec §3.7 fail-fast: a FAIL must not traverse a plain edge to a runs_on=success target"

    # End to end: a prose-answering agent reproduces the filed symptom exactly.
    backend = ContractFollowingBackend()
    engine = PipelineEngine(
        graph=parse_dot(dot),
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=str(tmp_path),
    )
    outcome = await engine.run()
    assert outcome.status is StageStatus.FAIL
    assert "No matching edge from node 'implement'" in (outcome.notes or "")
    assert engine.completed_nodes == ["start", "implement"]
