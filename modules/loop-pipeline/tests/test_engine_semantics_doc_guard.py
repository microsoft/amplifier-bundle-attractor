"""Behavior/source-anchored half of the context/engine-semantics.md drift guard
-- D-202a, D-202b.

The text-anchored half (D-200, D-201) moved to the repo-root opinionated-layer
harness at tests/test_engine_semantics_doc_guard.py (Track A of the repo split,
DESIGN-repo-split.md §1.4/§5#2). This half stayed here because it needs the live
engine: D-202a executes a real PipelineEngine run, and D-202b reads engine.py /
tool.py source as text to confirm the doc's cited behavior is grounded in code
that still exists nearby the cited lines. Both ride with loop-pipeline (the
engine) when it moves to the runner repo.

Reference: ``context/engine-semantics.md`` §3 Routing contract.
"""

import asyncio
from pathlib import Path

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


def _engine_src() -> str:
    return ENGINE_PATH.read_text()


def _tool_src() -> str:
    return TOOL_PATH.read_text()


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
