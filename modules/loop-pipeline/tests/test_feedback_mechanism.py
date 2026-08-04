"""Unit tests for the feedback_from= accumulation contract (EXTENSIONS.md §29).

Covers:
  1. collect_and_inject_feedback: no-op when no node declares feedback_from=
  2. collect_and_inject_feedback: collects tool.output from critic node
  3. collect_and_inject_feedback: iteration numbering ("Iteration N critique:")
  4. collect_and_inject_feedback: accumulates across iterations (co-location)
  5. collect_and_inject_feedback: curation bound (MAX_CRITIQUES oldest-drop)
  6. collect_and_inject_feedback: missing critic node is a no-op (no crash)
  7. collect_and_inject_feedback: truncates long critic output (MAX_CRITIQUE_CHARS)
  8. collect_and_inject_feedback: writes durable artifact to logs_root/feedback/
  9. collect_and_inject_feedback: durable artifact holds critiques from >=2 iterations
 10. collect_and_inject_feedback: per-target plain key (prior_critiques_<node_id>)
     expands in prompts; injection key is scoped to the target node
 11. Engine integration: feedback_from= fires on loop_restart, injects into context
 12. Engine integration: nodes without feedback_from= are untouched (backward compat)
 13. Engine integration: feedback survives loop_restart (context_updates untouched)
 14. Engine integration: 3-iteration loop accumulates critiques from >=2 iterations
     in a single durable artifact (the DoD co-location check)
 15. Multi-target isolation: two generator nodes with different critics receive only
     their own critic's history (no cross-target leakage — scoping regression guard)
 16. Exemplar regression: convergence-factory.dot generate node prompt expands
     $prior_critiques_generate (not the old $prior_critiques) after collect_and_inject.
 17. Delivery guarantee (unit): ensure_feedback_placeholder appends the labeled
     critique block when the prompt lacks the placeholder, and leaves the prompt
     untouched when the placeholder is present / no feedback_from= / empty channel.
 18. Delivery guarantee (engine integration): a target prompt that deliberately
     LACKS $prior_critiques_<node_id> still receives the iteration-numbered prior
     critique on iteration 2 (feedback_from= is a contract, not a prompt convention).
 19. No double injection: when the prompt DOES carry the placeholder, the critique
     appears exactly once (author-controlled placement; no appended block).
"""

from __future__ import annotations

import os

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.feedback import (
    _CHANNEL_KEY_PREFIX,
    MAX_CRITIQUE_CHARS,
    MAX_CRITIQUES,
    PRIOR_CRITIQUES_KEY,
    PRIOR_CRITIQUES_KEY_PREFIX,
    collect_and_inject_feedback,
    ensure_feedback_placeholder,
)
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_outcome_with_tool_output(text: str) -> Outcome:
    return Outcome(
        status=StageStatus.SUCCESS,
        is_explicit=True,
        context_updates={"tool.output": text, "tool.last_line": text.splitlines()[-1] if text else ""},
    )


def _make_outcome_with_notes(notes: str) -> Outcome:
    return Outcome(
        status=StageStatus.SUCCESS,
        notes=notes,
    )


def _make_graph_with_feedback(
    target_id: str = "work",
    critic_id: str = "critic",
) -> Graph:
    """Return a minimal graph where target declares feedback_from=critic."""
    return Graph(
        name="feedback-test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            target_id: Node(
                id=target_id,
                shape="parallelogram",
                attrs={"feedback_from": critic_id},
            ),
            critic_id: Node(id=critic_id, shape="parallelogram"),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node=target_id),
            Edge(from_node=target_id, to_node=critic_id),
            Edge(from_node=critic_id, to_node=target_id, loop_restart=True),
            Edge(from_node=critic_id, to_node="done"),
        ],
    )


def _make_graph_no_feedback() -> Graph:
    """Return a graph with no feedback_from= declarations."""
    return Graph(
        name="no-feedback-test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "work": Node(id="work", shape="parallelogram"),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="done"),
        ],
    )


# ---------------------------------------------------------------------------
# Unit tests: collect_and_inject_feedback (pure logic, no engine)
# ---------------------------------------------------------------------------


def test_no_op_when_no_feedback_from_declared(tmp_path):
    """Case 1: no node declares feedback_from= — context is untouched."""
    graph = _make_graph_no_feedback()
    context = PipelineContext()
    node_outcomes = {"work": _make_outcome_with_tool_output("some output")}

    collect_and_inject_feedback(
        graph=graph,
        node_outcomes=node_outcomes,
        context=context,
        iteration_count=1,
        logs_root=str(tmp_path),
    )

    # No feedback_from= declared: neither the legacy key nor any per-target key
    # should appear in context.
    assert context.get(PRIOR_CRITIQUES_KEY) is None, (
        "No feedback_from= declared: legacy prior_critiques must not appear in context"
    )
    assert context.get(PRIOR_CRITIQUES_KEY_PREFIX + "work") is None, (
        "No feedback_from= declared: per-target prior_critiques_work must not appear"
    )
    assert not (tmp_path / "feedback").exists(), (
        "No feedback_from= declared: feedback/ dir must not be created"
    )


def test_collects_tool_output_from_critic(tmp_path):
    """Case 2: collects tool.output from the named critic node."""
    graph = _make_graph_with_feedback()
    context = PipelineContext()
    node_outcomes = {
        "work": _make_outcome_with_tool_output("work output"),
        "critic": _make_outcome_with_tool_output("CRITIQUE-mark-1"),
    }

    collect_and_inject_feedback(
        graph=graph,
        node_outcomes=node_outcomes,
        context=context,
        iteration_count=1,
        logs_root=str(tmp_path),
    )

    # The per-target key for node "work" is "prior_critiques_work"
    prior = context.get(PRIOR_CRITIQUES_KEY_PREFIX + "work")
    assert prior is not None, "prior_critiques_work must be set after collection"
    assert "CRITIQUE-mark-1" in str(prior), (
        "Critic's tool.output must appear in prior_critiques_work"
    )


def test_iteration_numbering(tmp_path):
    """Case 3: injected critiques carry 'Iteration N critique:' labels."""
    graph = _make_graph_with_feedback()
    context = PipelineContext()
    node_outcomes = {"critic": _make_outcome_with_tool_output("first critique")}

    collect_and_inject_feedback(
        graph=graph,
        node_outcomes=node_outcomes,
        context=context,
        iteration_count=1,
        logs_root=str(tmp_path),
    )

    # The per-target key for node "work" is "prior_critiques_work"
    prior = str(context.get(PRIOR_CRITIQUES_KEY_PREFIX + "work") or "")
    assert "Iteration 1 critique:" in prior, (
        f"Expected 'Iteration 1 critique:' in prior_critiques_work, got: {prior!r}"
    )


def test_accumulates_across_iterations(tmp_path):
    """Case 4: critiques from multiple iterations co-exist in prior_critiques."""
    graph = _make_graph_with_feedback()
    context = PipelineContext()

    # Iteration 1
    collect_and_inject_feedback(
        graph=graph,
        node_outcomes={"critic": _make_outcome_with_tool_output("CRITIQUE-mark-1")},
        context=context,
        iteration_count=1,
        logs_root=str(tmp_path),
    )

    # Iteration 2
    collect_and_inject_feedback(
        graph=graph,
        node_outcomes={"critic": _make_outcome_with_tool_output("CRITIQUE-mark-2")},
        context=context,
        iteration_count=2,
        logs_root=str(tmp_path),
    )

    # The per-target key for node "work" is "prior_critiques_work"
    prior = str(context.get(PRIOR_CRITIQUES_KEY_PREFIX + "work") or "")
    assert "CRITIQUE-mark-1" in prior, "Iteration 1 critique must survive into iteration 2"
    assert "CRITIQUE-mark-2" in prior, "Iteration 2 critique must appear"
    assert "Iteration 1 critique:" in prior
    assert "Iteration 2 critique:" in prior


def test_curation_bound_drops_oldest(tmp_path):
    """Case 5: when channel exceeds MAX_CRITIQUES, oldest entries are dropped."""
    graph = _make_graph_with_feedback()
    context = PipelineContext()

    # Fill beyond the bound
    for i in range(1, MAX_CRITIQUES + 3):
        collect_and_inject_feedback(
            graph=graph,
            node_outcomes={"critic": _make_outcome_with_tool_output(f"CRITIQUE-mark-{i}")},
            context=context,
            iteration_count=i,
            logs_root=str(tmp_path),
        )

    # The per-target key for node "work" is "prior_critiques_work"
    prior = str(context.get(PRIOR_CRITIQUES_KEY_PREFIX + "work") or "")
    # The per-target channel key for node "work" is "feedback.channel.work"
    channel = context.get(_CHANNEL_KEY_PREFIX + "work")
    assert isinstance(channel, list), "Channel must be stored as a list"
    assert len(channel) <= MAX_CRITIQUES, (
        f"Channel depth {len(channel)} exceeds MAX_CRITIQUES={MAX_CRITIQUES}"
    )
    # Oldest entries should be gone; newest should be present
    assert f"CRITIQUE-mark-{MAX_CRITIQUES + 2}" in prior, "Latest critique must be present"
    assert "CRITIQUE-mark-1" not in prior, "Oldest critique must have been dropped"


def test_missing_critic_node_is_noop(tmp_path):
    """Case 6: critic node not in node_outcomes — no crash, no injection."""
    graph = _make_graph_with_feedback()
    context = PipelineContext()

    # node_outcomes has no "critic" key
    collect_and_inject_feedback(
        graph=graph,
        node_outcomes={"work": _make_outcome_with_tool_output("work output")},
        context=context,
        iteration_count=1,
        logs_root=str(tmp_path),
    )

    assert context.get(PRIOR_CRITIQUES_KEY_PREFIX + "work") is None, (
        "Missing critic node must not inject prior_critiques_work"
    )


def test_truncates_long_critic_output(tmp_path):
    """Case 7: critic output longer than MAX_CRITIQUE_CHARS is truncated."""
    graph = _make_graph_with_feedback()
    context = PipelineContext()
    long_critique = "X" * (MAX_CRITIQUE_CHARS + 100)

    collect_and_inject_feedback(
        graph=graph,
        node_outcomes={"critic": _make_outcome_with_tool_output(long_critique)},
        context=context,
        iteration_count=1,
        logs_root=str(tmp_path),
    )

    # The per-target key for node "work" is "prior_critiques_work"
    prior = str(context.get(PRIOR_CRITIQUES_KEY_PREFIX + "work") or "")
    # The injected text must be bounded
    assert len(prior) < len(long_critique) + 200, (
        "Long critique must be truncated in prior_critiques_work"
    )
    assert "[…truncated]" in prior or "truncated" in prior.lower(), (
        "Truncated critique must carry a truncation marker"
    )


def test_writes_durable_artifact(tmp_path):
    """Case 8: a durable artifact is written to logs_root/feedback/<target>.md."""
    graph = _make_graph_with_feedback()
    context = PipelineContext()

    collect_and_inject_feedback(
        graph=graph,
        node_outcomes={"critic": _make_outcome_with_tool_output("CRITIQUE-mark-1")},
        context=context,
        iteration_count=1,
        logs_root=str(tmp_path),
    )

    artifact = tmp_path / "feedback" / "work.md"
    assert artifact.exists(), f"Durable artifact must exist at {artifact}"
    content = artifact.read_text(encoding="utf-8")
    assert "CRITIQUE-mark-1" in content, "Artifact must contain the critique"


def test_durable_artifact_holds_multiple_iterations(tmp_path):
    """Case 9: durable artifact holds critiques from >=2 distinct iterations.

    This is the DoD co-location discriminator: Extension #24's per-iteration
    records scatter one critique per file; only accumulate-and-inject puts
    critiques from different iterations in the same artifact.
    """
    graph = _make_graph_with_feedback()
    context = PipelineContext()

    # Iteration 1
    collect_and_inject_feedback(
        graph=graph,
        node_outcomes={"critic": _make_outcome_with_tool_output("CRITIQUE-mark-1")},
        context=context,
        iteration_count=1,
        logs_root=str(tmp_path),
    )

    # Iteration 2
    collect_and_inject_feedback(
        graph=graph,
        node_outcomes={"critic": _make_outcome_with_tool_output("CRITIQUE-mark-2")},
        context=context,
        iteration_count=2,
        logs_root=str(tmp_path),
    )

    artifact = tmp_path / "feedback" / "work.md"
    assert artifact.exists(), "Durable artifact must exist"
    content = artifact.read_text(encoding="utf-8")

    assert "CRITIQUE-mark-1" in content, "Artifact must hold iteration 1 critique"
    assert "CRITIQUE-mark-2" in content, "Artifact must hold iteration 2 critique"
    assert "Iteration 1 critique:" in content, "Artifact must carry iteration 1 label"
    assert "Iteration 2 critique:" in content, "Artifact must carry iteration 2 label"


def test_prior_critiques_is_plain_key(tmp_path):
    """Case 10: per-target injection key is plain (non-dotted) — expands in prompts.

    The key format is ``prior_critiques_<node_id>`` (no dots), referenced in
    prompts as ``$prior_critiques_<node_id>``.  For target node "work" the key
    is "prior_critiques_work".
    """
    graph = _make_graph_with_feedback()
    context = PipelineContext()

    collect_and_inject_feedback(
        graph=graph,
        node_outcomes={"critic": _make_outcome_with_tool_output("some critique")},
        context=context,
        iteration_count=1,
        logs_root=str(tmp_path),
    )

    # The prefix itself must be plain (no dot) so the full key is also plain
    assert "." not in PRIOR_CRITIQUES_KEY_PREFIX, (
        f"PRIOR_CRITIQUES_KEY_PREFIX must not contain a dot, got: {PRIOR_CRITIQUES_KEY_PREFIX!r}"
    )
    # The full per-target key for node "work"
    target_key = PRIOR_CRITIQUES_KEY_PREFIX + "work"
    assert "." not in target_key, (
        f"Per-target injection key must be plain (no dot), got: {target_key!r}"
    )
    # Verify the key is accessible via snapshot (which _expand_variables reads)
    snapshot = context.snapshot()
    assert target_key in snapshot, (
        f"'{target_key}' must appear in context.snapshot() (the expand path)"
    )


def test_notes_fallback_when_no_tool_output(tmp_path):
    """collect_and_inject_feedback falls back to outcome.notes for codergen nodes."""
    graph = _make_graph_with_feedback()
    context = PipelineContext()

    collect_and_inject_feedback(
        graph=graph,
        node_outcomes={"critic": _make_outcome_with_notes("codergen critique notes")},
        context=context,
        iteration_count=1,
        logs_root=str(tmp_path),
    )

    # The per-target key for node "work" is "prior_critiques_work"
    prior = str(context.get(PRIOR_CRITIQUES_KEY_PREFIX + "work") or "")
    assert "codergen critique notes" in prior, (
        "Notes fallback: outcome.notes must appear in prior_critiques_work"
    )


# ---------------------------------------------------------------------------
# Engine integration tests
# ---------------------------------------------------------------------------


class FeedbackFixtureBackend:
    """Backend for engine integration tests.

    Simulates a 3-iteration loop with box (codergen) nodes:
    - 'work' node: always succeeds, records its prompt
    - 'critic' node: emits 'CRITIQUE-mark-N' for iterations 1 and 2 via
      preferred_label, then 'converged' on iteration 3

    The backend tracks which prompts were passed to 'work' so we can assert
    that prior_critiques were injected.
    """

    def __init__(self):
        self.work_calls = 0
        self.critic_calls = 0
        self.work_prompts: list[str] = []

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        if node.id == "work":
            self.work_calls += 1
            self.work_prompts.append(prompt or "")
            return Outcome(
                status=StageStatus.SUCCESS,
                is_explicit=True,
                notes=f"work-attempt-{self.work_calls}",
            )

        if node.id == "critic":
            self.critic_calls += 1
            n = self.critic_calls
            if n >= 3:
                label = "converged"
                notes = "CONVERGED"
            else:
                label = "refine"
                notes = f"CRITIQUE-mark-{n}"
            return Outcome(
                status=StageStatus.SUCCESS,
                is_explicit=True,
                preferred_label=label,
                notes=notes,
            )

        # start / done nodes
        return Outcome(status=StageStatus.SUCCESS, is_explicit=True)


def _make_feedback_graph(attr: str = "feedback_from") -> Graph:
    """Build a 3-iteration feedback fixture graph using box (codergen) nodes."""
    return Graph(
        name="feedback-fixture",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "work": Node(
                id="work",
                shape="box",
                prompt="Do work. Prior critiques: $prior_critiques_work",
                attrs={attr: "critic"},
            ),
            "critic": Node(
                id="critic",
                shape="box",
                prompt="Critique the work and return preferred_label=refine or converged.",
            ),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="critic"),
            Edge(
                from_node="critic",
                to_node="done",
                condition="preferred_label=converged",
            ),
            Edge(
                from_node="critic",
                to_node="work",
                condition="preferred_label=refine",
                loop_restart=True,
            ),
        ],
    )


@pytest.mark.asyncio
async def test_engine_feedback_fires_on_loop_restart(tmp_path):
    """Case 11: feedback_from= fires on loop_restart and injects into context."""
    backend = FeedbackFixtureBackend()
    graph = _make_feedback_graph()
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )

    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS), (
        f"Pipeline should succeed; got {outcome.status} — {outcome.failure_reason}"
    )
    # Critic ran at least twice (iterations 1 and 2 before CONVERGED on 3)
    assert backend.critic_calls >= 2, (
        f"Expected >=2 critic calls for 3-iteration loop; got {backend.critic_calls}"
    )
    # prior_critiques_work must have been injected (visible in context after run)
    # The per-target key for the "work" generator node is "prior_critiques_work"
    prior = context.get(PRIOR_CRITIQUES_KEY_PREFIX + "work")
    assert prior is not None, (
        "prior_critiques_work must be set in context after loop_restart with feedback_from="
    )


@pytest.mark.asyncio
async def test_engine_no_feedback_from_is_untouched(tmp_path):
    """Case 12: nodes without feedback_from= behave identically (backward compat)."""
    backend = FeedbackFixtureBackend()
    graph = Graph(
        name="no-feedback",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "work": Node(id="work", shape="box", prompt="Do work"),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="work"),
            Edge(from_node="work", to_node="done"),
        ],
    )
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )

    await engine.run()

    assert context.get(PRIOR_CRITIQUES_KEY_PREFIX + "work") is None, (
        "No feedback_from= declared: prior_critiques_work must not appear in context"
    )
    assert not (tmp_path / "feedback").exists(), (
        "No feedback_from= declared: feedback/ dir must not be created"
    )


@pytest.mark.asyncio
async def test_engine_feedback_survives_loop_restart(tmp_path):
    """Case 13: accumulated feedback survives loop_restart (context_updates untouched)."""
    backend = FeedbackFixtureBackend()
    graph = _make_feedback_graph()
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )

    await engine.run()

    # After the full run, the per-target channel for "work" must hold >=2 entries
    # Channel key: "feedback.channel.work" (per-target dotted key)
    channel = context.get(_CHANNEL_KEY_PREFIX + "work")
    assert isinstance(channel, list), "Channel must be stored as a list in context"
    assert len(channel) >= 2, (
        f"Channel must hold critiques from >=2 iterations; got {len(channel)}: {channel}"
    )


@pytest.mark.asyncio
async def test_engine_3iteration_durable_artifact_colocation(tmp_path):
    """Case 14: 3-iteration loop produces a durable artifact with critiques from >=2 iterations.

    This is the mechanical DoD co-location check: Extension #24 already
    scatters one critique per iteration in separate per-iteration records.
    Only accumulate-and-inject puts critiques from different iterations
    together in a single artifact.
    """
    backend = FeedbackFixtureBackend()
    graph = _make_feedback_graph()
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )

    await engine.run()

    # The durable artifact must exist
    artifact = tmp_path / "feedback" / "work.md"
    assert artifact.exists(), (
        f"Durable artifact must exist at {artifact}. "
        "The feedback mechanism must write the accumulated channel to disk."
    )

    content = artifact.read_text(encoding="utf-8")

    # Must contain critiques from >=2 distinct iterations (co-location check)
    import re
    distinct_marks = set(re.findall(r"CRITIQUE-mark-\d+", content))
    assert len(distinct_marks) >= 2, (
        f"Durable artifact must hold critiques from >=2 distinct iterations; "
        f"found marks: {distinct_marks}\nArtifact content:\n{content}"
    )

    # Must carry iteration numbering
    assert "iteration" in content.lower(), (
        "Durable artifact must carry iteration association (e.g. 'Iteration N critique:')"
    )


@pytest.mark.asyncio
async def test_engine_feedback_iteration_numbers_in_injected_text(tmp_path):
    """Iteration-numbered critiques appear in the injected prior_critiques text."""
    backend = FeedbackFixtureBackend()
    graph = _make_feedback_graph()
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )

    await engine.run()

    # The per-target key for the "work" generator node is "prior_critiques_work"
    prior = str(context.get(PRIOR_CRITIQUES_KEY_PREFIX + "work") or "")
    assert "Iteration 1 critique:" in prior, (
        f"Iteration 1 critique must appear in prior_critiques_work; got: {prior!r}"
    )
    assert "Iteration 2 critique:" in prior, (
        f"Iteration 2 critique must appear in prior_critiques_work; got: {prior!r}"
    )


# ---------------------------------------------------------------------------
# Delivery guarantee (critique-b B1 closure): declaring feedback_from= must be
# sufficient on its own — the placeholder controls placement, not delivery.
# ---------------------------------------------------------------------------


def test_placeholder_helper_no_feedback_from_unchanged():
    """Case 17a: nodes without feedback_from= get their prompt back unchanged."""
    node = Node(id="work", shape="box", prompt="Do work.")
    context = PipelineContext()
    assert ensure_feedback_placeholder(node, "Do work.", context) == "Do work."


def test_placeholder_helper_empty_channel_unchanged():
    """Case 17b: feedback_from= declared but nothing collected yet (iteration 0):
    prompt unchanged — no empty boilerplate block."""
    node = Node(
        id="work", shape="box", prompt="Do work.", attrs={"feedback_from": "critic"}
    )
    context = PipelineContext()
    assert ensure_feedback_placeholder(node, "Do work.", context) == "Do work."


def test_placeholder_helper_appends_when_missing():
    """Case 17c: channel has content and the prompt lacks the placeholder:
    a labeled block carrying $prior_critiques_<node_id> is appended."""
    node = Node(
        id="work", shape="box", prompt="Do work.", attrs={"feedback_from": "critic"}
    )
    context = PipelineContext()
    context.set(PRIOR_CRITIQUES_KEY_PREFIX + "work", "Iteration 1 critique: FIX-X")

    result = ensure_feedback_placeholder(node, "Do work.", context)

    assert result.startswith("Do work."), "Original prompt must be preserved"
    assert "$" + PRIOR_CRITIQUES_KEY_PREFIX + "work" in result, (
        "Appended block must carry the placeholder token so the normal P7 "
        f"expansion path injects the history; got: {result!r}"
    )
    assert "Prior critique history" in result, (
        f"Appended block must be labeled; got: {result!r}"
    )


def test_placeholder_helper_respects_author_placement():
    """Case 17d: prompt already references the placeholder: unchanged (author
    controls placement; no duplicate block)."""
    prompt = "Do work.\nHistory: $prior_critiques_work"
    node = Node(
        id="work", shape="box", prompt=prompt, attrs={"feedback_from": "critic"}
    )
    context = PipelineContext()
    context.set(PRIOR_CRITIQUES_KEY_PREFIX + "work", "Iteration 1 critique: FIX-X")

    assert ensure_feedback_placeholder(node, prompt, context) == prompt


@pytest.mark.asyncio
async def test_engine_injects_without_placeholder(tmp_path):
    """Case 18: the B1 closure integration test (critique-b's own repro shape).

    The target prompt deliberately LACKS $prior_critiques_work. Declaring
    feedback_from= alone must still deliver the iteration-numbered prior
    critique to the second-iteration prompt. Before the fix, the second
    prompt was byte-identical to the first ('Produce the artifact.') — the
    declared contract silently supplied no critique.
    """
    backend = FeedbackFixtureBackend()
    graph = _make_feedback_graph()
    # Remove the placeholder: delivery must not depend on it.
    graph.nodes["work"].prompt = "Produce the artifact."
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )

    outcome = await engine.run()

    assert outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
    assert len(backend.work_prompts) >= 2, (
        f"Loop must have restarted; got {len(backend.work_prompts)} work prompts"
    )
    second = backend.work_prompts[1]
    assert "CRITIQUE-mark-1" in second, (
        "feedback_from= must deliver the prior critique even when the prompt "
        f"lacks the placeholder; second prompt was: {second!r}"
    )
    assert "Iteration 1 critique:" in second, (
        f"Delivered critique must carry iteration numbering; got: {second!r}"
    )
    # The placeholder token itself must NOT leak into the final prompt
    assert "$prior_critiques_work" not in second, (
        f"Placeholder must be expanded, not shipped literally; got: {second!r}"
    )


@pytest.mark.asyncio
async def test_engine_no_double_injection_with_placeholder(tmp_path):
    """Case 19: when the prompt DOES carry the placeholder, the critique appears
    exactly once — author-controlled placement, no appended block on top."""
    backend = FeedbackFixtureBackend()
    graph = _make_feedback_graph()  # prompt carries $prior_critiques_work
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=str(tmp_path),
    )

    await engine.run()

    assert len(backend.work_prompts) >= 2
    second = backend.work_prompts[1]
    assert second.count("Iteration 1 critique:") == 1, (
        f"Critique must appear exactly once (no double injection); got: {second!r}"
    )
    assert "Prior critique history (engine-accumulated" not in second, (
        "The auto-appended block must not be added when the author placed the "
        f"placeholder; got: {second!r}"
    )


def test_multi_target_isolation_no_leakage(tmp_path):
    """Case 15: two generator nodes with different critics receive only their own
    critic's history — no cross-target leakage (scoping regression guard).

    Concrete reproduction of the unscoped-shared-state bug found in review:
    with shared keys, work_b received work_a's critique history.  With per-target
    keys, each generator's injection key is isolated.
    """
    # Graph with two independent generators, each with its own critic
    graph = Graph(
        name="two-target-isolation",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "work_a": Node(
                id="work_a",
                shape="box",
                attrs={"feedback_from": "critic_a"},
            ),
            "critic_a": Node(id="critic_a", shape="box"),
            "work_b": Node(
                id="work_b",
                shape="box",
                attrs={"feedback_from": "critic_b"},
            ),
            "critic_b": Node(id="critic_b", shape="box"),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[],
    )
    context = PipelineContext()

    # Simulate iteration 1: critic_a runs, critic_b does not
    collect_and_inject_feedback(
        graph=graph,
        node_outcomes={"critic_a": _make_outcome_with_tool_output("A ONLY")},
        context=context,
        iteration_count=1,
        logs_root=str(tmp_path),
    )

    # Simulate iteration 2: critic_b runs, critic_a does not
    collect_and_inject_feedback(
        graph=graph,
        node_outcomes={"critic_b": _make_outcome_with_tool_output("B ONLY")},
        context=context,
        iteration_count=2,
        logs_root=str(tmp_path),
    )

    key_a = PRIOR_CRITIQUES_KEY_PREFIX + "work_a"
    key_b = PRIOR_CRITIQUES_KEY_PREFIX + "work_b"

    prior_a = str(context.get(key_a) or "")
    prior_b = str(context.get(key_b) or "")

    # work_a's channel must contain A ONLY's critique
    assert "A ONLY" in prior_a, (
        f"work_a's injection key must contain critic_a's output; got: {prior_a!r}"
    )
    # work_b's channel must contain B ONLY's critique
    assert "B ONLY" in prior_b, (
        f"work_b's injection key must contain critic_b's output; got: {prior_b!r}"
    )

    # ISOLATION: work_b must NOT contain work_a's critique (the shared-key leakage bug)
    assert "A ONLY" not in prior_b, (
        f"LEAKAGE: work_b's injection key contains work_a's critique! "
        f"prior_critiques_work_b={prior_b!r}"
    )
    # ISOLATION: work_a must NOT contain work_b's critique
    assert "B ONLY" not in prior_a, (
        f"LEAKAGE: work_a's injection key contains work_b's critique! "
        f"prior_critiques_work_a={prior_a!r}"
    )

    # Each target's durable artifact must be separate
    artifact_a = tmp_path / "feedback" / "work_a.md"
    artifact_b = tmp_path / "feedback" / "work_b.md"
    if artifact_a.exists():
        content_a = artifact_a.read_text(encoding="utf-8")
        assert "B ONLY" not in content_a, (
            f"work_a.md must not contain critic_b's output; got: {content_a!r}"
        )
    if artifact_b.exists():
        content_b = artifact_b.read_text(encoding="utf-8")
        assert "A ONLY" not in content_b, (
            f"work_b.md must not contain critic_a's output; got: {content_b!r}"
        )


def test_convergence_factory_exemplar_prompt_expands_scoped_key(tmp_path):
    """Case 16: convergence-factory.dot generate node uses $prior_critiques_generate.

    Regression guard for an implementation-documentation mismatch found in
    review: the exemplar prompt must reference the per-target key
    $prior_critiques_generate (not the old $prior_critiques), so that after
    collect_and_inject_feedback the expanded prompt contains the injected critique.

    This test:
    1. Parses the shipped convergence-factory.dot exemplar.
    2. Simulates a loop_restart: calls collect_and_inject_feedback with a mock
       'feedback' node critique.
    3. Expands the 'generate' node's prompt via _expand_variables.
    4. Asserts the expanded prompt contains the injected critique text.
    5. Asserts the old $prior_critiques variable is NOT what delivers the critique
       (i.e., the exemplar correctly uses the scoped key).
    """
    from amplifier_module_loop_pipeline.handlers.codergen import _expand_variables

    # Locate the shipped exemplar
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    exemplar_path = os.path.join(
        repo_root, "examples", "patterns", "convergence-factory.dot"
    )
    assert os.path.exists(exemplar_path), (
        f"convergence-factory.dot not found at {exemplar_path}. "
        "This test guards the shipped exemplar's prompt variable name."
    )

    with open(exemplar_path, encoding="utf-8") as f:
        dot_source = f.read()

    graph = parse_dot(dot_source)

    # Verify the exemplar declares feedback_from= on the generate node
    generate_node = graph.nodes.get("generate")
    assert generate_node is not None, "convergence-factory.dot must have a 'generate' node"
    feedback_from = (generate_node.attrs or {}).get("feedback_from")
    assert feedback_from == "feedback", (
        f"generate node must declare feedback_from=\"feedback\"; got {feedback_from!r}"
    )

    # Simulate a loop_restart: collect the feedback node's critique
    context = PipelineContext()
    critique_text = "THE-INJECTED-CRITIQUE-SENTINEL"
    collect_and_inject_feedback(
        graph=graph,
        node_outcomes={
            "feedback": _make_outcome_with_notes(critique_text),
        },
        context=context,
        iteration_count=1,
        logs_root=str(tmp_path),
    )

    # The per-target key for node "generate" must be set
    per_target_key = PRIOR_CRITIQUES_KEY_PREFIX + "generate"  # "prior_critiques_generate"
    assert context.get(per_target_key) is not None, (
        f"collect_and_inject_feedback must set '{per_target_key}' in context; "
        "check that the 'generate' node's feedback_from= is correctly parsed."
    )

    # Expand the generate node's prompt with the context
    generate_prompt = generate_node.prompt or ""
    assert generate_prompt, "generate node must have a non-empty prompt"

    expanded = _expand_variables(generate_prompt, graph, context)

    # The expanded prompt must contain the injected critique
    assert critique_text in expanded, (
        f"The expanded generate prompt must contain the injected critique "
        f"'{critique_text}'. This means the prompt must reference "
        f"$prior_critiques_generate (the per-target scoped key), not $prior_critiques "
        f"(the old unscoped key).\n\nExpanded prompt:\n{expanded}"
    )

    # Confirm the old unscoped key is NOT what delivered the critique
    # (if it were, the old $prior_critiques would expand to the critique text,
    # which would mean the engine is writing the legacy key — a regression)
    old_key_value = context.get(PRIOR_CRITIQUES_KEY)  # "prior_critiques"
    assert old_key_value is None, (
        f"The engine must NOT write the legacy 'prior_critiques' key; "
        f"found: {old_key_value!r}. Per-target scoping requires only "
        f"'{per_target_key}' to be set."
    )
