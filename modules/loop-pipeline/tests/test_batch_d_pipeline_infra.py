"""Tests for Track 3 Batch D: Pipeline Infrastructure gaps.

Covers M-19, M-20, M-21, M-22, M-23, M-24, L-7, L-8, L-10, L-14/L-15, L-17.
"""

from __future__ import annotations

import json
import logging

import pytest

from amplifier_module_loop_pipeline.artifacts import ArtifactStore
from amplifier_module_loop_pipeline.checkpoint import (
    Checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.fidelity import (
    VALID_FIDELITY_MODES,
    build_preamble,
    resolve_fidelity,
)
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.interviewer import (
    Answer,
    AnswerValue,
    ConsoleInterviewer,
    Interviewer,
    Question,
    QuestionType,
    RecordingInterviewer,
)
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.retry import RetryPolicy
from amplifier_module_loop_pipeline.stylesheet import (
    apply_stylesheet,
    parse_stylesheet,
)
from amplifier_module_loop_pipeline.transforms import (
    Transform,
    apply_transforms,
    expand_variables,
)
from amplifier_module_loop_pipeline.run_identity import RunIdentity
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockBackend:
    def __init__(self, return_value: str = "done") -> None:
        self._return_value = return_value

    async def run(self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None) -> str:
        return self._return_value


def _make_engine(
    dot_source: str,
    backend: object | None = None,
    logs_root: str = "/tmp/test-batch-d",
) -> PipelineEngine:
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext(backend=backend))
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
    )


def _make_graph(**overrides) -> Graph:
    defaults = {
        "name": "test",
        "nodes": {
            "start": Node(id="start", shape="Mdiamond"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        "edges": [Edge(from_node="start", to_node="exit")],
    }
    defaults.update(overrides)
    return Graph(**defaults)


# ===========================================================================
# M-19: Status.json fields don't match spec
# ===========================================================================


class TestM19StatusJsonFields:
    """M-19: status.json must use 'outcome' field and include spec fields."""

    @pytest.mark.asyncio
    async def test_engine_status_json_has_outcome_field(self, tmp_path):
        """Engine _write_node_status uses 'outcome' instead of 'status'."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "work" / "status.json") as f:
            data = json.load(f)
        assert "outcome" in data
        assert data["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_engine_status_json_has_preferred_next_label(self, tmp_path):
        """Engine status.json includes preferred_next_label from Outcome."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "work" / "status.json") as f:
            data = json.load(f)
        # Field must exist (even if None)
        assert "preferred_next_label" in data

    @pytest.mark.asyncio
    async def test_engine_status_json_has_suggested_next_ids(self, tmp_path):
        """Engine status.json includes suggested_next_ids from Outcome."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "work" / "status.json") as f:
            data = json.load(f)
        assert "suggested_next_ids" in data

    @pytest.mark.asyncio
    async def test_engine_status_json_has_context_updates(self, tmp_path):
        """Engine status.json includes context_updates from Outcome."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "work" / "status.json") as f:
            data = json.load(f)
        assert "context_updates" in data

    @pytest.mark.asyncio
    async def test_engine_status_json_backward_compat_status(self, tmp_path):
        """Engine status.json still has 'status' for backward compatibility."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "work" / "status.json") as f:
            data = json.load(f)
        # backward compat: 'status' key still present
        assert "status" in data
        assert data["status"] == data["outcome"]

    @pytest.mark.asyncio
    async def test_codergen_status_json_has_outcome_field(self, tmp_path):
        """Codergen handler _write_status uses 'outcome' field."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        # The codergen handler also writes status.json; the engine overwrites it.
        # Both should use outcome. We just check the final file has outcome.
        with open(tmp_path / "work" / "status.json") as f:
            data = json.load(f)
        assert "outcome" in data

    @pytest.mark.asyncio
    async def test_engine_status_json_has_session_id_key(self, tmp_path):
        """Engine status.json always includes session_id (null when not set)."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "work" / "status.json") as f:
            data = json.load(f)
        assert "session_id" in data
        assert data["session_id"] is None

    @pytest.mark.asyncio
    async def test_engine_status_json_session_id_populated(self, tmp_path):
        """Engine status.json has session_id when outcome carries one."""

        class _SessionOutcomeBackend:
            async def run(
                self, node: Node, prompt: str, context: PipelineContext, incoming_edge=None, graph=None
            ) -> Outcome:
                if node.id == "work":
                    return Outcome(
                        status=StageStatus.SUCCESS,
                        session_id="child-sess-999",
                    )
                return Outcome(status=StageStatus.SUCCESS)

        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=_SessionOutcomeBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        with open(tmp_path / "work" / "status.json") as f:
            data = json.load(f)
        assert data["session_id"] == "child-sess-999"


# ===========================================================================
# M-20: No formal Transform interface
# ===========================================================================


class TestM20TransformProtocol:
    """M-20: Transform protocol exists and apply_transforms accepts custom transforms."""

    def test_transform_protocol_exists(self):
        """Transform protocol is importable from transforms module."""
        # Import already done at top; just verify it's a protocol-like thing
        assert hasattr(Transform, "apply")

    def test_builtin_transforms_implement_protocol(self):
        """Built-in expand_variables can be wrapped as a Transform."""
        # expand_variables matches the Transform.apply signature
        # when partially applied with context
        graph = _make_graph()
        context = PipelineContext()
        context.set("graph.goal", "test")

        class GoalExpander:
            def __init__(self, ctx: PipelineContext):
                self._ctx = ctx

            def apply(self, graph: Graph) -> Graph:
                return expand_variables(graph, self._ctx)

        expander = GoalExpander(context)
        result = expander.apply(graph)
        assert isinstance(result, Graph)

    def test_apply_transforms_accepts_custom_transforms(self):
        """apply_transforms can accept additional custom transforms."""
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "step": Node(id="step", prompt="original"),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="step"),
                Edge(from_node="step", to_node="exit"),
            ],
        )
        context = PipelineContext()

        class UpperCaseTransform:
            def apply(self, g: Graph) -> Graph:
                for node in g.nodes.values():
                    if node.prompt:
                        node.prompt = node.prompt.upper()
                return g

        result = apply_transforms(
            graph, context, extra_transforms=[UpperCaseTransform()]
        )
        assert result.nodes["step"].prompt == "ORIGINAL"

    def test_apply_transforms_runs_custom_after_builtin(self):
        """Custom transforms run after built-in ones."""
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "step": Node(id="step", prompt="Build $goal"),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="step"),
                Edge(from_node="step", to_node="exit"),
            ],
            goal="auth",
        )
        context = PipelineContext()
        context.set("graph.goal", "auth")

        class UpperCaseTransform:
            def apply(self, g: Graph) -> Graph:
                for node in g.nodes.values():
                    if node.prompt:
                        node.prompt = node.prompt.upper()
                return g

        result = apply_transforms(
            graph, context, extra_transforms=[UpperCaseTransform()]
        )
        # $goal expanded first, then uppercased
        assert result.nodes["step"].prompt == "BUILD AUTH"


# ===========================================================================
# M-21: Shape-name selectors missing from stylesheet
# ===========================================================================


class TestM21ShapeNameSelectors:
    """M-21: Stylesheet supports bare shape-name selectors (e.g. 'box')."""

    def test_parse_shape_selector(self):
        """Bare shape name is parsed as a valid selector."""
        rules = parse_stylesheet("box { llm_model: gpt-4; }")
        assert len(rules) == 1
        assert rules[0].selector == "box"

    def test_shape_selector_specificity(self):
        """Shape selectors have specificity between universal and class.

        Specificity order: universal(0) < shape(1) < class(2) < id(3).
        """
        rules = parse_stylesheet("""
            * { llm_model: universal; }
            box { llm_model: shape; }
            .code { llm_model: class; }
            #special { llm_model: id; }
        """)
        specificities = {r.selector: r.specificity for r in rules}
        assert specificities["*"] < specificities["box"]
        assert specificities["box"] < specificities[".code"]
        assert specificities[".code"] < specificities["#special"]

    def test_shape_selector_matches_node_shape(self):
        """Shape selector matches nodes with that shape attribute."""
        graph = Graph(
            name="test",
            nodes={
                "code_node": Node(id="code_node", shape="box", prompt="Code"),
                "human_node": Node(id="human_node", shape="ellipse", prompt="Review"),
            },
            edges=[],
        )
        rules = parse_stylesheet("box { llm_model: box-model; }")
        apply_stylesheet(graph, rules)
        assert graph.nodes["code_node"].attrs.get("llm_model") == "box-model"
        assert graph.nodes["human_node"].attrs.get("llm_model") is None

    def test_shape_selector_overridden_by_class(self):
        """Class selector overrides shape selector."""
        graph = Graph(
            name="test",
            nodes={
                "n": Node(id="n", shape="box", prompt="X", attrs={"class": "special"}),
            },
            edges=[],
        )
        rules = parse_stylesheet("""
            box { llm_model: shape-model; }
            .special { llm_model: class-model; }
        """)
        apply_stylesheet(graph, rules)
        assert graph.nodes["n"].attrs.get("llm_model") == "class-model"

    def test_shape_selector_overrides_universal(self):
        """Shape selector overrides universal selector."""
        graph = Graph(
            name="test",
            nodes={
                "n": Node(id="n", shape="box", prompt="X"),
            },
            edges=[],
        )
        rules = parse_stylesheet("""
            * { llm_model: universal-model; }
            box { llm_model: box-model; }
        """)
        apply_stylesheet(graph, rules)
        assert graph.nodes["n"].attrs.get("llm_model") == "box-model"


# ===========================================================================
# M-22: Fidelity mode validation silent fallback
# ===========================================================================


class TestM22FidelityValidation:
    """M-22: Invalid fidelity modes log a WARNING instead of silent fallback."""

    def test_invalid_fidelity_logs_warning(self, caplog):
        """Unrecognized fidelity mode logs a WARNING."""
        node = Node(id="n", prompt="X", attrs={"fidelity": "ful"})
        graph = _make_graph()
        with caplog.at_level(logging.WARNING):
            result = resolve_fidelity(node, None, graph)
        # Should still fall back to compact
        assert result == "compact"
        # But should have logged a warning
        assert any("ful" in record.message for record in caplog.records)

    def test_valid_fidelity_no_warning(self, caplog):
        """Valid fidelity modes do not log warnings."""
        for mode in VALID_FIDELITY_MODES:
            caplog.clear()
            node = Node(id="n", prompt="X", attrs={"fidelity": mode})
            graph = _make_graph()
            with caplog.at_level(logging.WARNING):
                result = resolve_fidelity(node, None, graph)
            assert result == mode
            warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
            assert len(warnings) == 0, f"Unexpected warning for valid mode '{mode}'"

    def test_build_preamble_invalid_mode_logs_warning(self, caplog):
        """build_preamble also warns on invalid fidelity mode."""
        ctx = PipelineContext()
        ctx.set("graph.goal", "test")
        with caplog.at_level(logging.WARNING):
            result = build_preamble("typo_mode", ctx, {})
        # Should fall back to compact behavior
        assert "test" in result
        assert any("typo_mode" in record.message for record in caplog.records)


# ===========================================================================
# M-23: Checkpoint resume fidelity degradation
# ===========================================================================


class TestM23CheckpointFidelityDegradation:
    """M-23: Resuming from checkpoint degrades 'full' fidelity to 'summary:high'."""

    @pytest.mark.asyncio
    async def test_full_fidelity_degraded_on_resume(self, tmp_path):
        """When checkpoint has full fidelity, it degrades to summary:high for one hop then restores."""
        dot_source = """
            digraph {
                goal = "build auth"
                default_fidelity = "full"
                start [shape=Mdiamond]
                plan [prompt="Plan"]
                implement [prompt="Build"]
                exit [shape=Msquare]
                start -> plan -> implement -> exit
            }
            """
        identity = RunIdentity.from_graph(parse_dot(dot_source))

        cp = Checkpoint(
            current_node="plan",
            completed_nodes={"start": "success", "plan": "success"},
            context_snapshot={
                "graph.goal": "build auth",
                "outcome": "success",
                "graph.default_fidelity": "full",
            },
            node_outcomes={
                "start": {"status": "success"},
                "plan": {"status": "success"},
            },
            timestamp="2025-01-01T00:00:00Z",
            identity=identity,
        )
        save_checkpoint(cp, str(tmp_path / "checkpoint.json"))

        engine = _make_engine(
            dot_source=dot_source, backend=MockBackend(), logs_root=str(tmp_path)
        )
        await engine.run()
        # After resume, fidelity is degraded for the first hop then restored to full
        fidelity = engine.context.get("graph.default_fidelity")
        assert fidelity == "full"

    @pytest.mark.asyncio
    async def test_non_full_fidelity_not_degraded_on_resume(self, tmp_path):
        """When checkpoint has non-full fidelity, it's not changed."""
        dot_source = """
            digraph {
                goal = "build auth"
                default_fidelity = "compact"
                start [shape=Mdiamond]
                plan [prompt="Plan"]
                implement [prompt="Build"]
                exit [shape=Msquare]
                start -> plan -> implement -> exit
            }
            """
        identity = RunIdentity.from_graph(parse_dot(dot_source))

        cp = Checkpoint(
            current_node="plan",
            completed_nodes={"start": "success", "plan": "success"},
            context_snapshot={
                "graph.goal": "build auth",
                "outcome": "success",
                "graph.default_fidelity": "compact",
            },
            node_outcomes={
                "start": {"status": "success"},
                "plan": {"status": "success"},
            },
            timestamp="2025-01-01T00:00:00Z",
            identity=identity,
        )
        save_checkpoint(cp, str(tmp_path / "checkpoint.json"))

        engine = _make_engine(
            dot_source=dot_source, backend=MockBackend(), logs_root=str(tmp_path)
        )
        await engine.run()
        fidelity = engine.context.get("graph.default_fidelity")
        assert fidelity == "compact"

    @pytest.mark.asyncio
    async def test_fidelity_restored_after_first_node(self, tmp_path):
        """Fidelity degraded to summary:high for first post-resume node, then restored to full."""
        fidelities_seen: list[str] = []

        class FidelityCapturingBackend:
            async def run(self, node, prompt, context, incoming_edge=None, graph=None):
                fidelity = context.get("graph.default_fidelity")
                fidelities_seen.append(fidelity)
                return "done"

        dot_source = """
            digraph {
                goal = "build auth"
                default_fidelity = "full"
                start [shape=Mdiamond]
                plan [prompt="Plan"]
                implement [prompt="Build"]
                review [prompt="Review"]
                exit [shape=Msquare]
                start -> plan -> implement -> review -> exit
            }
            """
        identity = RunIdentity.from_graph(parse_dot(dot_source))

        cp = Checkpoint(
            current_node="plan",
            completed_nodes={"start": "success", "plan": "success"},
            context_snapshot={
                "graph.goal": "build auth",
                "outcome": "success",
                "graph.default_fidelity": "full",
            },
            node_outcomes={
                "start": {"status": "success"},
                "plan": {"status": "success"},
            },
            timestamp="2025-01-01T00:00:00Z",
            identity=identity,
        )
        save_checkpoint(cp, str(tmp_path / "checkpoint.json"))

        engine = _make_engine(
            dot_source=dot_source,
            backend=FidelityCapturingBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        # First post-resume node (implement) should run at degraded fidelity
        assert fidelities_seen[0] == "summary:high"
        # Second post-resume node (review) should run at restored full fidelity
        assert fidelities_seen[1] == "full"


# ===========================================================================
# M-24: Missing ConsoleInterviewer and RecordingInterviewer
# ===========================================================================


class TestM24Interviewers:
    """M-24: ConsoleInterviewer and RecordingInterviewer exist."""

    def test_console_interviewer_implements_protocol(self):
        """ConsoleInterviewer satisfies the Interviewer protocol."""
        ci = ConsoleInterviewer()
        assert isinstance(ci, Interviewer)

    def test_recording_interviewer_implements_protocol(self):
        """RecordingInterviewer satisfies the Interviewer protocol."""
        ri = RecordingInterviewer()
        assert isinstance(ri, Interviewer)

    def test_recording_interviewer_records_interactions(self):
        """RecordingInterviewer records all questions asked."""
        ri = RecordingInterviewer()
        q1 = Question(text="Approve?", type=QuestionType.YES_NO)
        q2 = Question(text="Choose:", type=QuestionType.FREEFORM)
        ri.ask(q1)
        ri.ask(q2)
        recordings = ri.get_recordings()
        assert len(recordings) == 2
        assert recordings[0][0].text == "Approve?"
        assert recordings[1][0].text == "Choose:"

    def test_recording_interviewer_with_preset_answers(self):
        """RecordingInterviewer can be seeded with preset answers."""
        answers = [
            Answer(value=AnswerValue.YES),
            Answer(value="custom text", text="my answer"),
        ]
        ri = RecordingInterviewer(answers=answers)
        q1 = Question(text="Approve?", type=QuestionType.YES_NO)
        q2 = Question(text="Describe:", type=QuestionType.FREEFORM)
        a1 = ri.ask(q1)
        a2 = ri.ask(q2)
        assert a1.value == AnswerValue.YES
        assert a2.text == "my answer"

    def test_recording_interviewer_exhausted_returns_skipped(self):
        """RecordingInterviewer returns SKIPPED when preset answers exhausted."""
        ri = RecordingInterviewer(answers=[Answer(value=AnswerValue.YES)])
        q1 = Question(text="First?", type=QuestionType.YES_NO)
        q2 = Question(text="Second?", type=QuestionType.YES_NO)
        ri.ask(q1)
        a2 = ri.ask(q2)
        assert a2.value == AnswerValue.SKIPPED

    def test_recording_interviewer_replay(self):
        """RecordingInterviewer recordings can be used to replay."""
        # Record
        ri = RecordingInterviewer(answers=[Answer(value=AnswerValue.YES)])
        q = Question(text="Approve?", type=QuestionType.YES_NO)
        ri.ask(q)
        recordings = ri.get_recordings()

        # Each recording is (question, answer) tuple
        assert len(recordings) == 1
        assert recordings[0][0].text == "Approve?"
        assert recordings[0][1].value == AnswerValue.YES


# ===========================================================================
# L-7: Checkpoint missing logs field
# ===========================================================================


class TestL7CheckpointLogs:
    """L-7: Checkpoint includes a logs field."""

    def test_checkpoint_has_logs_field(self):
        """Checkpoint dataclass has a logs field."""
        cp = Checkpoint(
            current_node="step",
            completed_nodes={},
            context_snapshot={},
            node_outcomes={},
            timestamp="2025-01-01T00:00:00Z",
            logs=["Node start completed", "Node plan started"],
        )
        assert cp.logs == ["Node start completed", "Node plan started"]

    def test_checkpoint_logs_default_empty(self):
        """Checkpoint logs defaults to empty list."""
        cp = Checkpoint(
            current_node="step",
            completed_nodes={},
            context_snapshot={},
            node_outcomes={},
            timestamp="2025-01-01T00:00:00Z",
        )
        assert cp.logs == []

    def test_checkpoint_logs_serialized(self, tmp_path):
        """Checkpoint logs are saved and loaded from JSON."""
        cp = Checkpoint(
            current_node="step",
            completed_nodes={},
            context_snapshot={},
            node_outcomes={},
            timestamp="2025-01-01T00:00:00Z",
            logs=["event1", "event2"],
        )
        path = str(tmp_path / "checkpoint.json")
        save_checkpoint(cp, path)
        loaded = load_checkpoint(path)
        assert loaded.logs == ["event1", "event2"]

    @pytest.mark.asyncio
    async def test_engine_saves_logs_in_checkpoint(self, tmp_path):
        """Engine includes context logs in checkpoint."""
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        # Manually add a log entry to context
        engine.context.append_log("Test log entry")
        await engine.run()

        data = json.loads((tmp_path / "checkpoint.json").read_text())
        assert "logs" in data
        assert "Test log entry" in data["logs"]


# ===========================================================================
# L-8: Checkpoint completed_nodes dict vs spec's list
# ===========================================================================


class TestL8CheckpointCompletedNodeList:
    """L-8: Checkpoint provides a completed_node_list property."""

    def test_completed_node_list_property(self):
        """Checkpoint has completed_node_list property returning list of IDs."""
        cp = Checkpoint(
            current_node="impl",
            completed_nodes={"start": "success", "plan": "success"},
            context_snapshot={},
            node_outcomes={},
            timestamp="2025-01-01T00:00:00Z",
        )
        node_list = cp.completed_node_list
        assert isinstance(node_list, list)
        assert set(node_list) == {"start", "plan"}

    def test_completed_node_list_empty(self):
        """completed_node_list returns empty list when no nodes."""
        cp = Checkpoint(
            current_node="start",
            completed_nodes={},
            context_snapshot={},
            node_outcomes={},
            timestamp="2025-01-01T00:00:00Z",
        )
        assert cp.completed_node_list == []


# ===========================================================================
# L-10: Artifact store missing has/remove/clear
# ===========================================================================


class TestL10ArtifactStoreExtendedAPI:
    """L-10: ArtifactStore has has(), remove(), and clear() methods."""

    def test_has_returns_true_for_existing(self, tmp_path):
        """has() returns True for stored artifacts."""
        store = ArtifactStore(base_dir=str(tmp_path))
        store.store("item", "data")
        assert store.has("item") is True

    def test_has_returns_false_for_missing(self, tmp_path):
        """has() returns False for non-existent artifacts."""
        store = ArtifactStore(base_dir=str(tmp_path))
        assert store.has("missing") is False

    def test_remove_deletes_artifact(self, tmp_path):
        """remove() deletes an artifact from the store."""
        store = ArtifactStore(base_dir=str(tmp_path))
        store.store("item", "data")
        store.remove("item")
        assert store.has("item") is False
        assert store.get("item") is None

    def test_remove_missing_is_noop(self, tmp_path):
        """remove() on non-existent artifact does not raise."""
        store = ArtifactStore(base_dir=str(tmp_path))
        store.remove("nonexistent")  # should not raise

    def test_clear_removes_all(self, tmp_path):
        """clear() removes all artifacts."""
        store = ArtifactStore(base_dir=str(tmp_path))
        store.store("a", "data1")
        store.store("b", "data2")
        store.store("c", "data3")
        store.clear()
        assert store.list() == []
        assert store.has("a") is False

    def test_remove_file_backed_artifact(self, tmp_path):
        """remove() cleans up file-backed artifacts."""
        store = ArtifactStore(base_dir=str(tmp_path))
        large_data = "x" * 150_000
        store.store("big", large_data)
        artifact_path = tmp_path / "artifacts" / "big.json"
        assert artifact_path.exists()
        store.remove("big")
        assert store.has("big") is False
        # File should be cleaned up too
        assert not artifact_path.exists()


# ===========================================================================
# L-14/L-15: Retry counter tracking + preset policies
# ===========================================================================


class TestL14RetryCounter:
    """L-14: Retry counter tracked in context."""

    @pytest.mark.asyncio
    async def test_retry_count_stored_in_context(self, tmp_path):
        """After execution, retry count is available in context."""
        # This is a basic test that the retry counter key exists
        engine = _make_engine(
            dot_source="""
            digraph {
                start [shape=Mdiamond]
                work [prompt="Do work"]
                exit [shape=Msquare]
                start -> work -> exit
            }
            """,
            backend=MockBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()
        # On success with no retries, the counter should be 0
        count = engine.context.get("internal.retry_count.work", 0)
        assert count == 0


class TestL15PresetPolicies:
    """L-15: Named preset retry policies."""

    def test_preset_none(self):
        """'none' preset: 1 attempt (no retries)."""
        policy = RetryPolicy.from_preset("none")
        assert policy.max_attempts == 1

    def test_preset_standard(self):
        """'standard' preset: 5 attempts, 200ms initial delay (spec §3.5)."""
        policy = RetryPolicy.from_preset("standard")
        assert policy.max_attempts == 5

    def test_preset_aggressive(self):
        """'aggressive' preset: 5 attempts."""
        policy = RetryPolicy.from_preset("aggressive")
        assert policy.max_attempts == 5

    def test_preset_linear(self):
        """'linear' preset: 3 attempts, linear backoff."""
        policy = RetryPolicy.from_preset("linear")
        assert policy.max_attempts == 3
        assert policy.backoff.backoff_factor == 1.0

    def test_preset_patient(self):
        """'patient' preset: 3 attempts, 2000ms initial delay, factor 3.0 (spec §3.5)."""
        policy = RetryPolicy.from_preset("patient")
        assert policy.max_attempts == 3

    def test_unknown_preset_raises(self):
        """Unknown preset name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown"):
            RetryPolicy.from_preset("nonexistent")


# ===========================================================================
# L-17: Variable expansion duplicated in 3 places
# ===========================================================================


class TestL17VariableExpansionConsolidated:
    """L-17: Variable expansion uses a single shared utility."""

    def test_codergen_uses_shared_expansion(self, tmp_path):
        """Codergen handler uses shared expand_goal_variable."""
        from amplifier_module_loop_pipeline.handlers.codergen import _expand_variables

        graph = Graph(
            name="test",
            nodes={"s": Node(id="s", shape="Mdiamond")},
            edges=[],
            goal="build auth",
        )
        context = PipelineContext()
        context.set("graph.goal", "build auth")
        result = _expand_variables("Plan $goal", graph, context)
        assert result == "Plan build auth"

    def test_shared_expansion_uses_context_first(self):
        """Shared expansion prefers context value over graph.goal."""
        from amplifier_module_loop_pipeline.transforms import expand_goal_variable

        result = expand_goal_variable("Build $goal", "graph-goal", "context-goal")
        assert result == "Build context-goal"

    def test_shared_expansion_falls_back_to_graph_goal(self):
        """Shared expansion falls back to graph goal when context is empty."""
        from amplifier_module_loop_pipeline.transforms import expand_goal_variable

        result = expand_goal_variable("Build $goal", "graph-goal", "")
        assert result == "Build graph-goal"

    def test_shared_expansion_no_goal_unchanged(self):
        """When no goal available, $goal is left unchanged."""
        from amplifier_module_loop_pipeline.transforms import expand_goal_variable

        result = expand_goal_variable("Build $goal", "", "")
        assert "$goal" in result
