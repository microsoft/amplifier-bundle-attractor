"""Drift guard for context/engine-semantics.md — D-200, D-201, D-202.

Guards against doc/engine drift in ``context/engine-semantics.md``, the
bundle's declared source of truth for shipped-engine behavior.  Two claim
classes are checked:

  (a) **Text-anchored** (shape b): regex the doc for the claim and assert
      the corrected language is present.  These verify that the *words* are
      there, not that the *behavior* is correct.  They catch edits that
      silently revert a correction.

  (b) **Behavior-anchored** (shape a): execute a minimal graph or inspect
      source and assert the doc's claim holds in the running code.  These
      verify actual engine/handler behavior.

Honest limits:
  - Text-anchored checks (D-200, D-201) can be fooled by wording changes
    that preserve the wrong meaning.  They are a last-resort gate, not a
    substitute for behavioral tests.
  - The no-matching-edge behavioral check (D-202a) exercises the main loop
    via a full engine run.  The companion ``test_edge_selection_no_silent_
    fallthrough.py`` covers ``select_edge()`` at the unit level.
  - The stale-label rule (D-201) has no dedicated behavioral test here: the
    bug only manifests on the *second visit* to a gate in a corrective loop,
    which requires a full looping graph — deferred to a future behavioral
    test (or lint).  The text-anchored check is the right tradeoff for now.
  - Source-inspection checks (D-202b) verify that the cited line numbers in
    the doc are in the right ballpark; they are NOT cite-freshness checks
    (which would break on every harmless refactor) but sanity checks that
    the referenced behavior exists at a nearby line.

Reference: ``context/engine-semantics.md`` §3 Routing contract.
Paired behavioral anchors: ``test_edge_selection_no_silent_fallthrough.py``,
``test_engine.py::test_no_matching_edge_returns_fail``.
"""

import asyncio
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUNDLE_ROOT = Path(__file__).parent.parent.parent.parent
DOC_PATH = BUNDLE_ROOT / "context" / "engine-semantics.md"
ENGINE_PATH = (
    BUNDLE_ROOT
    / "modules"
    / "loop-pipeline"
    / "amplifier_module_loop_pipeline"
    / "engine.py"
)
TOOL_PATH = (
    BUNDLE_ROOT
    / "modules"
    / "loop-pipeline"
    / "amplifier_module_loop_pipeline"
    / "handlers"
    / "tool.py"
)


def _doc() -> str:
    return DOC_PATH.read_text()


def _engine_src() -> str:
    return ENGINE_PATH.read_text()


def _tool_src() -> str:
    return TOOL_PATH.read_text()


# ---------------------------------------------------------------------------
# D-200: No-matching-edge claim — text-anchored (shape b)
#
# The doc must NOT contain the stale "does NOT hard-fail" assertion.
# The doc MUST document the main-loop hard-fail AND distinguish the subgraph
# path.  Both halves are required; over-correcting to "always hard-fails"
# would be wrong in the other direction.
# ---------------------------------------------------------------------------


def test_d200_stale_no_matching_edge_claim_absent():
    """D-200a (text-anchored): the stale 'does NOT hard-fail' assertion must
    be gone from engine-semantics.md.

    The main loop DOES hard-fail on no-matching-edge (engine.py:773-788,
    PIPELINE_ERROR error_type=no_matching_edge; shipped behavior since the
    initial engine commit).  Presence of the old claim means the doc has
    regressed.
    """
    doc = _doc()
    assert not re.search(r"does NOT hard-?fail", doc, re.IGNORECASE), (
        "engine-semantics.md still contains the stale 'does NOT hard-fail "
        "no_matching_edge' claim. The main loop DOES hard-fail "
        "(engine.py:773-788). Remove or correct this claim. (D-200a)"
    )


def test_d200_no_matching_edge_main_loop_documented():
    """D-200b (text-anchored): the doc must mention no_matching_edge behavior."""
    doc = _doc()
    assert re.search(r"no.matching.edge|no_matching_edge", doc, re.IGNORECASE), (
        "engine-semantics.md does not mention no-matching-edge behavior at all. "
        "The main loop hard-fails with PIPELINE_ERROR error_type=no_matching_edge "
        "(engine.py:773-788). Add the corrected claim. (D-200b)"
    )


def test_d200_subgraph_path_distinguished():
    """D-200c (text-anchored): the doc must distinguish the subgraph path.

    run_subgraph now distinguishes two dead-end cases:
    - Conditional-mismatch (outgoing edges exist but none matched): returns FAIL
      with a non-empty failure_reason (issue-172 fix; EXTENSIONS.md §33 update).
    - No outgoing edges at all (designed terminus): returns last outcome unchanged.
    Both halves must be stated to avoid authors over-trusting the hard-fail
    inside parallel/manager layers.
    """
    doc = _doc()
    assert re.search(r"subgraph|run_subgraph|parallel branch", doc, re.IGNORECASE), (
        "engine-semantics.md documents no-matching-edge behavior but never "
        "distinguishes the subgraph path. run_subgraph distinguishes conditional-"
        "mismatch dead ends (FAIL with failure_reason) from designed termini "
        "(last outcome unchanged). Both halves must be stated. (D-200c)"
    )


# ---------------------------------------------------------------------------
# D-201: Stale-label rule — text-anchored (shape b)
#
# The doc must document that a failing tool node does NOT refresh
# tool.last_line.  The key retains the value from the last successful run.
# On the second visit to a gate, a stale label can match a
# context.tool.last_line=X edge even when the current run failed, causing
# an unintended parallel fan-out.
# ---------------------------------------------------------------------------


def test_d201_stale_label_rule_documented():
    """D-201 (text-anchored): the stale-label rule must be documented near
    tool.last_line in engine-semantics.md.

    Failing tool nodes do NOT refresh tool.last_line (early FAIL return at
    tool.py:158-176 precedes the context.set at tool.py:220).  Without this
    rule documented, authors write 'context.tool.last_line=X' edges beside
    'outcome=fail' edges and get a parallel fan-out on the second visit to
    the gate.  (D-201)
    """
    doc = _doc()
    # Find the context around 'last_line' and check for staleness language
    last_line_sections = []
    for m in re.finditer(r"last_line", doc, re.IGNORECASE):
        start = max(0, m.start() - 300)
        end = min(len(doc), m.end() + 300)
        last_line_sections.append(doc[start:end])

    assert last_line_sections, (
        "engine-semantics.md does not mention tool.last_line at all. "
        "The token channel section is missing. (D-201)"
    )

    staleness_pattern = re.compile(
        r"stale|not refresh|does not refresh|not updated|retain|preserv|previous",
        re.IGNORECASE,
    )
    found = any(staleness_pattern.search(section) for section in last_line_sections)
    assert found, (
        "engine-semantics.md mentions tool.last_line but does not document the "
        "stale-label rule: a failing tool node does NOT refresh tool.last_line "
        "(early FAIL return at tool.py:158-176 precedes context.set at tool.py:220). "
        "Add the staleness caveat and the '&& outcome=success' discipline. (D-201)"
    )


# ---------------------------------------------------------------------------
# D-202a: No-matching-edge hard-fail — behavior-anchored (shape a)
#
# The main loop MUST emit PIPELINE_ERROR with error_type=no_matching_edge
# and return a FAIL outcome when a non-terminal node has no matching
# outgoing edge.  This is the behavioral claim the doc makes.
#
# Paired with: test_edge_selection_no_silent_fallthrough.py (select_edge
# unit level) and test_engine.py::test_no_matching_edge_returns_fail.
# This test verifies the PIPELINE_ERROR event is also emitted.
# ---------------------------------------------------------------------------


def test_d202a_main_loop_hard_fails_no_matching_edge(tmp_path):
    """D-202a (behavior-anchored): main loop emits PIPELINE_ERROR with
    error_type=no_matching_edge and returns FAIL when a non-terminal node
    has no matching outgoing edge.

    engine-semantics.md §3 claims: 'Main loop hard-fails with
    terminate_pipeline, emits PIPELINE_ERROR with error_type: no_matching_edge.'
    This test verifies that claim against the running engine.

    Note: this does NOT test the subgraph path (run_subgraph now distinguishes
    conditional-mismatch dead ends from designed termini — see issue-172 and
    EXTENSIONS.md §33).  The doc documents both halves; this test covers the
    main-loop half.  (D-202a)
    """
    from typing import Any

    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.engine import PipelineEngine
    from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
    from amplifier_module_loop_pipeline.handlers import HandlerRegistry
    from amplifier_module_loop_pipeline.handlers.context import HandlerContext
    from amplifier_module_loop_pipeline.outcome import StageStatus
    from amplifier_module_loop_pipeline.pipeline_events import PIPELINE_ERROR

    class _MockBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            return "ok"

    class _CapturingHooks:
        """Minimal hooks object that captures emitted events."""

        def __init__(self) -> None:
            self.events: list[tuple[str, dict[str, Any]]] = []

        async def emit(self, event_name: str, data: dict[str, Any]) -> None:
            self.events.append((event_name, data))

        def get(self, event_name: str) -> list[dict[str, Any]]:
            return [d for n, d in self.events if n == event_name]

    # Build a graph where a codergen node has no outgoing edges.
    # Bypass the validator (which would reject isolated nodes) by building
    # the graph directly.
    graph = Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "dead_end": Node(id="dead_end", prompt="work"),
        },
        edges=[
            Edge(from_node="start", to_node="dead_end"),
            # dead_end has NO outgoing edges — main loop must hard-fail
        ],
    )

    hooks = _CapturingHooks()
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=_MockBackend()))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
        hooks=hooks,
    )

    outcome = asyncio.run(engine.run())

    # Claim 1: outcome must be FAIL (not silent SUCCESS)
    assert outcome.status == StageStatus.FAIL, (
        f"engine-semantics.md §3 claims the main loop hard-fails on no-matching-edge. "
        f"Expected FAIL, got {outcome.status}. (D-202a)"
    )

    # Claim 2: PIPELINE_ERROR event with error_type=no_matching_edge must be emitted
    pipeline_errors = hooks.get(PIPELINE_ERROR)
    no_matching_edge_errors = [
        e for e in pipeline_errors if e.get("error_type") == "no_matching_edge"
    ]
    assert no_matching_edge_errors, (
        "engine-semantics.md §3 claims the main loop emits PIPELINE_ERROR with "
        "error_type=no_matching_edge. No such event was emitted. "
        f"All pipeline:error events: {pipeline_errors}. (D-202a)"
    )


# ---------------------------------------------------------------------------
# D-202b: Source-inspection checks (shape c, lite)
#
# Verify that the behaviors the doc cites actually exist in the source at
# roughly the cited locations.  These are NOT line-exact cite-freshness
# checks (which would break on every harmless refactor) — they verify that
# the key symbols/patterns exist in the source file at all, confirming the
# doc's claims are grounded in real code.
# ---------------------------------------------------------------------------


def test_d202b_engine_emits_no_matching_edge_error():
    """D-202b (source-inspection): engine.py must contain the no_matching_edge
    PIPELINE_ERROR emission that the doc cites.

    engine-semantics.md §3 cites engine.py:773-788 for the hard-fail.
    This check verifies the cited behavior exists in the source (not the
    exact line numbers, which may shift on refactors).  (D-202b)
    """
    src = _engine_src()
    assert "no_matching_edge" in src, (
        "engine.py does not contain 'no_matching_edge'. "
        "engine-semantics.md §3 cites this as the error_type emitted on "
        "no-matching-edge hard-fail (engine.py:773-788). The doc's claim is "
        "now unsupported. (D-202b)"
    )
    assert "terminate_pipeline" in src, (
        "engine.py does not contain 'terminate_pipeline'. "
        "engine-semantics.md §3 cites this as the mechanism for the hard-fail. "
        "The doc's claim is now unsupported. (D-202b)"
    )


def test_d202b_tool_handler_sets_last_line_only_on_success():
    """D-202b (source-inspection): tool.py must set tool.last_line only on
    the success path, not on the early FAIL return.

    engine-semantics.md §3 cites tool.py:158-176 for the early FAIL return
    and tool.py:220 for the context.set.  This check verifies that
    'tool.last_line' appears in the source AFTER the returncode check,
    confirming the stale-label rule is grounded in real code.  (D-202b)
    """
    src = _tool_src()

    # The early FAIL return pattern must exist
    assert "returncode != 0" in src or "returncode" in src, (
        "tool.py does not contain a returncode check. "
        "engine-semantics.md §3 cites tool.py:158-176 for the early FAIL return "
        "that prevents tool.last_line from being refreshed on failure. (D-202b)"
    )

    # tool.last_line must be set in the source
    assert 'context.set("tool.last_line"' in src or "tool.last_line" in src, (
        "tool.py does not set tool.last_line. "
        "engine-semantics.md §3 cites tool.py:220 for this. (D-202b)"
    )

    # The key structural invariant: context.set("tool.last_line") must appear
    # AFTER the returncode != 0 block in the file (i.e., on the success path).
    fail_return_pos = src.find("returncode != 0")
    last_line_set_pos = src.find('context.set("tool.last_line"')

    assert fail_return_pos != -1, "tool.py: 'returncode != 0' check not found. (D-202b)"
    assert last_line_set_pos != -1, (
        'tool.py: context.set("tool.last_line") not found. (D-202b)'
    )
    assert last_line_set_pos > fail_return_pos, (
        "tool.py: context.set('tool.last_line') appears BEFORE the "
        "'returncode != 0' check. The stale-label rule documented in "
        "engine-semantics.md §3 requires that tool.last_line is set only "
        "on the success path (after the early FAIL return). "
        f"returncode check at offset {fail_return_pos}, "
        f"context.set at offset {last_line_set_pos}. (D-202b)"
    )
