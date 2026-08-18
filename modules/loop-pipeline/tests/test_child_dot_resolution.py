"""Issue #200 -- a missing child ``dot_file=`` is a RESOLUTION fault, not a routing one.

Before this change, a ``shape=folder`` node whose ``dot_file=`` named no
existing child graph returned ``Outcome(FAIL, "Child DOT file not found: X")``.
FAIL is fail-fast at edge selection, so a parent graph with no failure edge
terminated with::

    [PIPELINE] x Error at child (no_matching_edge): Child DOT file not found: ...
    attractor: notes:
    No matching edge from node 'child'

-- a routing framing for a missing FILE, printing only the single chosen path
so "resolved against the wrong base directory" had to be inferred.

What this file pins:

* the node-entry error class (``ChildDotResolutionError``) names the node, the
  literal ``dot_file=`` value, and EVERY tier of the EXTENSIONS.md section 10
  precedence chain;
* the engine reports it as ``error_type="child_dot_resolution"`` and the
  ``no_matching_edge`` framing is GONE (this is the RED-against-main test --
  on main the run's notes read "No matching edge from node 'child'");
* the load-bearing compat proof: WRITE-THEN-RUN COMPOSITION STILL WORKS.
  Admission stays LAZY (no existence check in ``validate_or_raise``), so a
  node that writes ``gen/child.dot`` mid-run and a later folder node that
  executes it still succeed end to end.
"""

from __future__ import annotations

import os

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.handlers.pipeline import (
    ChildDotResolutionError,
    PipelineHandler,
    resolve_dot_path,
    resolve_dot_path_candidates,
)
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.validation import validate, validate_or_raise

# A child graph that leaves a durable artifact, so "the child really ran" is
# provable from the filesystem rather than from the outcome's self-report.
_CHILD_DOT = """\
digraph generated_child {
    cstart [shape=Mdiamond]
    work   [shape=parallelogram, tool_command="printf child-evidence-200 > child-proof.txt"]
    cdone  [shape=Msquare]
    cstart -> work -> cdone
}
"""


def _make_engine(graph: Graph, logs_root: str, context: PipelineContext | None = None):
    return PipelineEngine(
        graph=graph,
        context=context if context is not None else PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=None)),
        logs_root=logs_root,
    )


def _folder_parent(source_dir: str, dot_file: str) -> Graph:
    """start -> child(folder) -> done, with NO failure edge (the issue's graph)."""
    return Graph(
        name="parent",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "child": Node(id="child", shape="folder", attrs={"dot_file": dot_file}),
            "done": Node(id="done", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="child"),
            Edge(from_node="child", to_node="done"),
        ],
        source_dir=source_dir,
    )


# ---------------------------------------------------------------------------
# resolve_dot_path_candidates() -- the diagnostic sibling of resolve_dot_path()
# ---------------------------------------------------------------------------


class TestResolveDotPathCandidates:
    """The candidate chain must describe exactly what resolve_dot_path() does."""

    def test_chosen_candidate_is_what_resolve_dot_path_returns(self) -> None:
        ctx = PipelineContext()
        ctx.set("context.target_dir", "/workspace")
        candidates = resolve_dot_path_candidates("child.dot", "/graphs", ctx)
        chosen = [c for c in candidates if c.chosen]
        assert len(chosen) == 1
        assert chosen[0].path == resolve_dot_path("child.dot", "/graphs", ctx)

    def test_all_four_tiers_are_reported_in_precedence_order(self) -> None:
        ctx = PipelineContext()
        ctx.set("context.target_dir", "/workspace")
        candidates = resolve_dot_path_candidates("child.dot", "/graphs", ctx)
        assert [c.tier for c in candidates] == [
            "absolute path",
            "graph.source_dir",
            "context.target_dir",
            "os.getcwd()",
        ]
        # source_dir wins; the lower tiers are reported but not chosen.
        assert candidates[1].chosen
        assert candidates[1].path == "/graphs/child.dot"
        assert candidates[2].path == "/workspace/child.dot"
        assert candidates[3].path == os.path.join(os.getcwd(), "child.dot")
        assert not any(c.chosen for c in (candidates[0], candidates[2], candidates[3]))

    def test_absolute_target_stops_the_chain_at_tier_one(self) -> None:
        """os.path.join(base, "/abs") is "/abs" for any base -- tiers 2-4 are n/a."""
        ctx = PipelineContext()
        ctx.set("context.target_dir", "/workspace")
        candidates = resolve_dot_path_candidates("/abs/child.dot", "/graphs", ctx)
        assert candidates[0].chosen and candidates[0].path == "/abs/child.dot"
        for lower in candidates[1:]:
            assert lower.path is None
            assert "absolute" in lower.unavailable_reason

    def test_unavailable_tiers_say_why(self) -> None:
        ctx = PipelineContext()  # no context.target_dir
        candidates = resolve_dot_path_candidates("child.dot", "", ctx)
        assert candidates[1].path is None
        assert "graph.source_dir is empty" in candidates[1].unavailable_reason
        assert candidates[2].path is None
        assert "context.target_dir is unset" in candidates[2].unavailable_reason
        assert candidates[3].chosen  # cwd is the last resort

    def test_variable_expansion_happens_before_the_chain(self) -> None:
        ctx = PipelineContext()
        ctx.set("lane", "bugfix")
        candidates = resolve_dot_path_candidates("$lane/child.dot", "/graphs", ctx)
        assert candidates[1].chosen
        assert candidates[1].path == "/graphs/bugfix/child.dot"

    def test_exists_is_computed_per_candidate(self, tmp_path) -> None:
        (tmp_path / "child.dot").write_text(_CHILD_DOT)
        ctx = PipelineContext()
        candidates = resolve_dot_path_candidates("child.dot", str(tmp_path), ctx)
        assert candidates[1].exists is True
        candidates = resolve_dot_path_candidates("nope.dot", str(tmp_path), ctx)
        assert candidates[1].exists is False


# ---------------------------------------------------------------------------
# The node-entry error itself
# ---------------------------------------------------------------------------


class TestChildDotResolutionErrorAtNodeEntry:
    """The handler raises a DISTINCT resolution error, with the full chain."""

    @pytest.mark.asyncio
    async def test_missing_child_raises_resolution_error_naming_every_candidate(
        self, tmp_path
    ) -> None:
        graph = _folder_parent(str(tmp_path), "missing-child.dot")
        handler = PipelineHandler()

        with pytest.raises(ChildDotResolutionError) as excinfo:
            await handler.execute(
                graph.nodes["child"],
                PipelineContext(),
                graph,
                str(tmp_path / "logs"),
            )

        exc = excinfo.value
        assert exc.node_id == "child"
        assert exc.dot_file == "missing-child.dot"

        message = str(exc)
        # Names the node and the LITERAL dot_file= value.
        assert "node 'child'" in message
        assert 'dot_file="missing-child.dot"' in message
        # Names every candidate path that was tried.
        for tier in (
            "absolute path",
            "graph.source_dir",
            "context.target_dir",
            "os.getcwd()",
        ):
            assert tier in message
        assert str(tmp_path / "missing-child.dot") in message
        assert "[CHOSEN, missing]" in message
        # And says, in words, that this is not a routing problem.
        assert "RESOLUTION failure, not an edge-routing failure" in message

    @pytest.mark.asyncio
    async def test_wrong_base_directory_is_readable_not_inferred(
        self, tmp_path
    ) -> None:
        """The child exists -- under a tier the precedence chain never reached."""
        graphs_dir = tmp_path / "graphs"
        workspace = tmp_path / "workspace"
        graphs_dir.mkdir()
        workspace.mkdir()
        (workspace / "child.dot").write_text(_CHILD_DOT)

        graph = _folder_parent(str(graphs_dir), "child.dot")
        context = PipelineContext()
        context.set("context.target_dir", str(workspace))

        with pytest.raises(ChildDotResolutionError) as excinfo:
            await PipelineHandler().execute(
                graph.nodes["child"], context, graph, str(tmp_path / "logs")
            )

        message = str(excinfo.value)
        assert "wrong base directory" in message
        assert f"context.target_dir -> {workspace / 'child.dot'}" in message
        assert "[not consulted, EXISTS]" in message

    @pytest.mark.asyncio
    async def test_present_child_still_executes_normally(self, tmp_path) -> None:
        """The existence assertion must not disturb the happy path."""
        (tmp_path / "child.dot").write_text(_CHILD_DOT)
        graph = _folder_parent(str(tmp_path), "child.dot")

        outcome = await PipelineHandler().execute(
            graph.nodes["child"],
            PipelineContext(),
            graph,
            str(tmp_path / "logs"),
        )
        assert outcome.status == StageStatus.SUCCESS


# ---------------------------------------------------------------------------
# The engine's framing -- this is the RED-against-main assertion
# ---------------------------------------------------------------------------


class TestEngineReportsAChildResolutionFaultNotARoutingFault:
    @pytest.mark.asyncio
    async def test_routing_framing_is_gone(self, tmp_path) -> None:
        """On main this run's notes read "No matching edge from node 'child'".

        (Named without the literal rule token on purpose: pytest builds
        ``tmp_path`` from the test's own name, and the paths this diagnostic
        prints would then contain the very substring the assertions forbid.)
        """
        events: list[tuple[str, dict]] = []

        class _Hooks:
            async def emit(self, name, data):
                events.append((name, data))

        graph = _folder_parent(str(tmp_path), "missing-child.dot")
        engine = PipelineEngine(
            graph=graph,
            context=PipelineContext(),
            handler_registry=HandlerRegistry(HandlerContext(backend=None)),
            logs_root=str(tmp_path / "logs"),
            hooks=_Hooks(),
        )

        outcome = await engine.run(goal="prove the framing")

        # Still a failure -- a missing child is still a failure, just a legible one.
        assert outcome.status == StageStatus.FAIL
        notes = f"{outcome.notes or ''}\n{outcome.failure_reason or ''}"
        assert "No matching edge" not in notes
        assert "no_matching_edge" not in notes
        assert "Child DOT file not found for node 'child'" in notes
        assert "graph.source_dir" in notes  # the candidate chain came along

        error_events = [d for name, d in events if name == "pipeline:error"]
        assert error_events, "expected a pipeline:error event"
        assert error_events[-1]["error_type"] == "child_dot_resolution"
        assert all(d["error_type"] != "no_matching_edge" for d in error_events)

    @pytest.mark.asyncio
    async def test_resolution_fault_is_not_retried(self, tmp_path) -> None:
        """max_retries cannot conjure the file; the fault stays terminal and clear."""
        graph = _folder_parent(str(tmp_path), "missing-child.dot")
        graph.nodes["child"].attrs["max_retries"] = "2"

        outcome = await _make_engine(graph, str(tmp_path / "logs")).run(goal="g")

        assert outcome.status == StageStatus.FAIL
        assert "Child DOT file not found for node 'child'" in (
            outcome.failure_reason or ""
        )


# ---------------------------------------------------------------------------
# The load-bearing compat proof: write-then-run composition still works
# ---------------------------------------------------------------------------


class TestWriteThenRunCompositionStillWorks:
    """EXTENSIONS.md section 10 admission stays LAZY -- issue #200 must not change that."""

    def test_admission_does_not_check_existence(self, tmp_path) -> None:
        """validate()/validate_or_raise() stay silent about an absent dot_file=."""
        graph = _folder_parent(str(tmp_path), "gen/child.dot")

        diags = validate(graph)
        assert not [d for d in diags if d.severity == "ERROR"]
        # Must not raise -- a composition graph has to be admitted to run at all.
        validate_or_raise(graph)

    @pytest.mark.asyncio
    async def test_node_writes_child_dot_and_a_later_folder_node_runs_it(
        self, tmp_path
    ) -> None:
        """The 3-node shape from the F2 compat finding, run end to end."""
        workdir = tmp_path / "work"
        workdir.mkdir()
        child_source = workdir / "gen" / "child.dot"
        assert not child_source.exists()  # absent at parse AND validate time

        # A tool node writes the child graph at run time.  cwd is the tool
        # handler's working directory, so write via an absolute path.
        write_cmd = (
            f"mkdir -p {child_source.parent} && "
            f"cat > {child_source} <<'CHILDEOF'\n{_CHILD_DOT}CHILDEOF\n"
            f"printf composed"
        )

        graph = Graph(
            name="write_then_run",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "compose": Node(
                    id="compose",
                    shape="parallelogram",
                    attrs={"tool_command": write_cmd},
                ),
                "child": Node(
                    id="child", shape="folder", attrs={"dot_file": "gen/child.dot"}
                ),
                "done": Node(id="done", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="compose"),
                Edge(from_node="compose", to_node="child"),
                Edge(from_node="child", to_node="done"),
            ],
            source_dir=str(workdir),
        )
        validate_or_raise(graph)  # admitted with the target still absent

        context = PipelineContext()
        context.set("context.target_dir", str(workdir))
        outcome = await _make_engine(
            graph, str(tmp_path / "logs"), context=context
        ).run(goal="compose then run")

        assert outcome.status == StageStatus.SUCCESS, outcome.failure_reason
        assert child_source.exists(), "the composer never wrote the child graph"
        # The child really ran: its own artifact is on disk.
        proof = workdir / "child-proof.txt"
        assert proof.exists(), "the composed child did not execute"
        assert proof.read_text() == "child-evidence-200"
