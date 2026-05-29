"""Tests for the wait-for-human gate handler and interviewer.

Spec coverage: HUMAN-001–008, INTV-001–010, Section 6.
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.handlers.human import HumanGateHandler
from amplifier_module_loop_pipeline.interviewer import (
    Answer,
    AnswerValue,
    AutoApproveInterviewer,
    CallbackInterviewer,
    Option,
    Question,
    QuestionType,
    QueueInterviewer,
)
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


def _make_graph_with_human_gate() -> Graph:
    """Graph with a human gate node and two outgoing edges."""
    return Graph(
        name="test",
        nodes={
            "start": Node(id="start", shape="Mdiamond"),
            "review": Node(
                id="review",
                shape="hexagon",
                label="Approve changes?",
                attrs={"prompt": "Do you approve this code?"},
            ),
            "deploy": Node(id="deploy", prompt="Deploy to prod"),
            "fix": Node(id="fix", prompt="Fix issues"),
            "exit": Node(id="exit", shape="Msquare"),
        },
        edges=[
            Edge(from_node="start", to_node="review"),
            Edge(from_node="review", to_node="deploy", label="Approve"),
            Edge(from_node="review", to_node="fix", label="Reject"),
            Edge(from_node="deploy", to_node="exit"),
            Edge(from_node="fix", to_node="exit"),
        ],
    )


def _make_context() -> PipelineContext:
    return PipelineContext()


# --- Interviewer models ---


class TestQuestionModel:
    """INTV-001: Question model has required fields."""

    def test_question_has_text_and_type(self):
        q = Question(text="Approve?", type=QuestionType.MULTIPLE_CHOICE)
        assert q.text == "Approve?"
        assert q.type == QuestionType.MULTIPLE_CHOICE

    def test_question_with_options(self):
        opts = [
            Option(key="Y", label="Yes, approve"),
            Option(key="N", label="No, reject"),
        ]
        q = Question(
            text="Approve?",
            type=QuestionType.MULTIPLE_CHOICE,
            options=opts,
            stage="review",
        )
        assert len(q.options) == 2
        assert q.options[0].key == "Y"
        assert q.stage == "review"

    def test_question_with_timeout(self):
        q = Question(
            text="Approve?",
            type=QuestionType.YES_NO,
            timeout_seconds=30.0,
        )
        assert q.timeout_seconds == 30.0


class TestAnswerModel:
    """INTV-002: Answer model."""

    def test_answer_with_value(self):
        a = Answer(value=AnswerValue.YES)
        assert a.value == AnswerValue.YES

    def test_answer_with_selected_option(self):
        opt = Option(key="Y", label="Yes")
        a = Answer(value="Y", selected_option=opt)
        assert a.selected_option is not None
        assert a.selected_option.key == "Y"

    def test_answer_with_text(self):
        a = Answer(value="custom", text="My feedback")
        assert a.text == "My feedback"


# --- AutoApproveInterviewer ---


class TestAutoApproveInterviewer:
    """INTV-004: AutoApproveInterviewer for automated testing."""

    def test_yes_no_returns_yes(self):
        interviewer = AutoApproveInterviewer()
        q = Question(text="Continue?", type=QuestionType.YES_NO)
        answer = interviewer.ask(q)
        assert answer.value == AnswerValue.YES

    def test_confirmation_returns_yes(self):
        interviewer = AutoApproveInterviewer()
        q = Question(text="Confirm?", type=QuestionType.CONFIRMATION)
        answer = interviewer.ask(q)
        assert answer.value == AnswerValue.YES

    def test_multiple_choice_returns_first_option(self):
        interviewer = AutoApproveInterviewer()
        opts = [
            Option(key="A", label="Approve"),
            Option(key="R", label="Reject"),
        ]
        q = Question(text="Choose", type=QuestionType.MULTIPLE_CHOICE, options=opts)
        answer = interviewer.ask(q)
        assert answer.value == "A"
        assert answer.selected_option is not None
        assert answer.selected_option.key == "A"

    def test_freeform_returns_auto_approved(self):
        interviewer = AutoApproveInterviewer()
        q = Question(text="Comments?", type=QuestionType.FREEFORM)
        answer = interviewer.ask(q)
        assert answer.text == "auto-approved"


# --- QueueInterviewer ---


class TestQueueInterviewer:
    """INTV-007: QueueInterviewer for deterministic testing."""

    def test_returns_queued_answers(self):
        answers = [
            Answer(value=AnswerValue.YES),
            Answer(value=AnswerValue.NO),
        ]
        interviewer = QueueInterviewer(answers)
        a1 = interviewer.ask(Question(text="Q1", type=QuestionType.YES_NO))
        a2 = interviewer.ask(Question(text="Q2", type=QuestionType.YES_NO))
        assert a1.value == AnswerValue.YES
        assert a2.value == AnswerValue.NO

    def test_empty_queue_returns_skipped(self):
        interviewer = QueueInterviewer([])
        answer = interviewer.ask(Question(text="Q", type=QuestionType.YES_NO))
        assert answer.value == AnswerValue.SKIPPED


# --- CallbackInterviewer ---


class TestCallbackInterviewer:
    """INTV-006: CallbackInterviewer delegates to callback."""

    def test_delegates_to_callback(self):
        def my_callback(q: Question) -> Answer:
            return Answer(value=AnswerValue.NO)

        interviewer = CallbackInterviewer(my_callback)
        answer = interviewer.ask(Question(text="Ok?", type=QuestionType.YES_NO))
        assert answer.value == AnswerValue.NO


# --- L-18: ask_multiple and inform on Interviewer ---


class TestInterviewerAskMultiple:
    """L-18: Interviewer implementations support ask_multiple."""

    def test_auto_approve_ask_multiple(self):
        interviewer = AutoApproveInterviewer()
        questions = [
            Question(text="Q1", type=QuestionType.YES_NO),
            Question(text="Q2", type=QuestionType.CONFIRMATION),
        ]
        answers = interviewer.ask_multiple(questions)
        assert len(answers) == 2
        assert answers[0].value == AnswerValue.YES
        assert answers[1].value == AnswerValue.YES

    def test_queue_ask_multiple(self):
        interviewer = QueueInterviewer(
            [
                Answer(value=AnswerValue.YES),
                Answer(value=AnswerValue.NO),
            ]
        )
        questions = [
            Question(text="Q1", type=QuestionType.YES_NO),
            Question(text="Q2", type=QuestionType.YES_NO),
        ]
        answers = interviewer.ask_multiple(questions)
        assert len(answers) == 2
        assert answers[0].value == AnswerValue.YES
        assert answers[1].value == AnswerValue.NO

    def test_callback_ask_multiple(self):
        def my_cb(q: Question) -> Answer:
            return Answer(value=AnswerValue.YES)

        interviewer = CallbackInterviewer(my_cb)
        questions = [
            Question(text="Q1", type=QuestionType.YES_NO),
            Question(text="Q2", type=QuestionType.YES_NO),
        ]
        answers = interviewer.ask_multiple(questions)
        assert len(answers) == 2


class TestInterviewerInform:
    """L-18: Interviewer implementations support inform."""

    def test_auto_approve_inform_does_not_raise(self):
        interviewer = AutoApproveInterviewer()
        # inform is fire-and-forget; should not raise
        interviewer.inform("Pipeline completed successfully")

    def test_queue_inform_does_not_raise(self):
        interviewer = QueueInterviewer([])
        interviewer.inform("Status update")

    def test_callback_inform_does_not_raise(self):
        interviewer = CallbackInterviewer(lambda q: Answer(value=AnswerValue.YES))
        interviewer.inform("Notification")


# --- HumanGateHandler ---


class TestHumanGateHandler:
    """HUMAN-001–008: Wait-for-human handler."""

    @pytest.mark.asyncio
    async def test_derives_choices_from_outgoing_edges(self):
        """HUMAN-002: Choices come from outgoing edge labels."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]
        handler = HumanGateHandler(interviewer=AutoApproveInterviewer())
        outcome = await handler.execute(node, _make_context(), graph, "/tmp")
        # AutoApprove picks the first choice; either way it should succeed
        assert outcome.status == StageStatus.SUCCESS
        # M-12: suggested_next_ids should contain a valid target node
        assert outcome.suggested_next_ids is not None
        assert outcome.suggested_next_ids[0] in ("deploy", "fix")

    @pytest.mark.asyncio
    async def test_auto_approve_picks_first_choice(self):
        """HUMAN-003: AutoApprove picks first edge label -> deploy node."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]
        handler = HumanGateHandler(interviewer=AutoApproveInterviewer())
        outcome = await handler.execute(node, _make_context(), graph, "/tmp")
        # M-12: maps "Approve" label to "deploy" target node
        assert outcome.suggested_next_ids == ["deploy"]

    @pytest.mark.asyncio
    async def test_queue_interviewer_selects_specific_choice(self):
        """HUMAN-004: QueueInterviewer routes based on queued answer."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]
        # Queue an answer that matches the second option ("Reject")
        interviewer = QueueInterviewer(
            [
                Answer(
                    value="Reject", selected_option=Option(key="Reject", label="Reject")
                ),
            ]
        )
        handler = HumanGateHandler(interviewer=interviewer)
        outcome = await handler.execute(node, _make_context(), graph, "/tmp")
        assert outcome.status == StageStatus.SUCCESS
        # M-12: "Reject" maps to "fix" target node
        assert outcome.suggested_next_ids == ["fix"]

    @pytest.mark.asyncio
    async def test_skipped_answer_returns_fail(self):
        """M-13: SKIPPED answer returns FAIL per spec, not SUCCESS."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]
        interviewer = QueueInterviewer([])  # Will return SKIPPED
        handler = HumanGateHandler(interviewer=interviewer)
        outcome = await handler.execute(node, _make_context(), graph, "/tmp")
        assert outcome.status == StageStatus.FAIL
        assert "skipped" in (outcome.notes or outcome.failure_reason or "").lower()

    @pytest.mark.asyncio
    async def test_timeout_answer_uses_default_choice(self):
        """HUMAN-006: TIMEOUT answer falls back to default choice."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]
        interviewer = QueueInterviewer([Answer(value=AnswerValue.TIMEOUT)])
        handler = HumanGateHandler(interviewer=interviewer)
        outcome = await handler.execute(node, _make_context(), graph, "/tmp")
        assert outcome.status == StageStatus.SUCCESS
        # M-12: default "Approve" maps to "deploy" target node
        assert outcome.suggested_next_ids == ["deploy"]

    @pytest.mark.asyncio
    async def test_uses_prompt_attr_for_question_text(self):
        """HUMAN-007: Question text comes from node prompt attr or label."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]
        captured_questions: list[Question] = []

        def capture_callback(q: Question) -> Answer:
            captured_questions.append(q)
            return Answer(
                value="Approve", selected_option=Option(key="Approve", label="Approve")
            )

        handler = HumanGateHandler(interviewer=CallbackInterviewer(capture_callback))
        await handler.execute(node, _make_context(), graph, "/tmp")
        assert len(captured_questions) == 1
        assert (
            "approve" in captured_questions[0].text.lower()
            or "code" in captured_questions[0].text.lower()
        )

    @pytest.mark.asyncio
    async def test_no_outgoing_edges_returns_success(self):
        """HUMAN-008: No outgoing edges still completes (edge case)."""
        graph = Graph(
            name="test",
            nodes={
                "start": Node(id="start", shape="Mdiamond"),
                "gate": Node(id="gate", shape="hexagon", label="Wait"),
                "exit": Node(id="exit", shape="Msquare"),
            },
            edges=[
                Edge(from_node="start", to_node="gate"),
                Edge(from_node="gate", to_node="exit"),  # no label
            ],
        )
        handler = HumanGateHandler(interviewer=AutoApproveInterviewer())
        outcome = await handler.execute(
            graph.nodes["gate"], _make_context(), graph, "/tmp"
        )
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_no_interviewer_raises_valueerror(self):
        """When no interviewer is provided, execute() raises ValueError."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]
        handler = HumanGateHandler()  # No interviewer arg
        with pytest.raises(
            ValueError, match="HumanGateHandler requires an Interviewer"
        ):
            await handler.execute(node, _make_context(), graph, "/tmp")

    @pytest.mark.asyncio
    async def test_sets_context_updates_with_spec_keys(self):
        """L-16: Handler sets human.gate.selected and human.gate.label per spec."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]
        handler = HumanGateHandler(interviewer=AutoApproveInterviewer())
        outcome = await handler.execute(node, _make_context(), graph, "/tmp")
        assert outcome.context_updates is not None
        # L-16: spec says human.gate.selected (not human.gate.selection)
        assert "human.gate.selected" in outcome.context_updates
        assert outcome.context_updates["human.gate.selected"] == "Approve"
        # L-16: spec says human.gate.label (not human.gate.node_id)
        assert "human.gate.label" in outcome.context_updates
        assert outcome.context_updates["human.gate.label"] == "Approve changes?"


# --- M-12: suggested_next_ids instead of preferred_label ---


class TestHumanGateSuggestedNextIds:
    """M-12: Human handler returns suggested_next_ids for unambiguous routing."""

    @pytest.mark.asyncio
    async def test_auto_approve_returns_suggested_next_ids(self):
        """AutoApprove selects first edge; outcome has suggested_next_ids=[target node]."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]
        handler = HumanGateHandler(interviewer=AutoApproveInterviewer())
        outcome = await handler.execute(node, _make_context(), graph, "/tmp")
        assert outcome.status == StageStatus.SUCCESS
        # "Approve" edge goes to "deploy"
        assert outcome.suggested_next_ids == ["deploy"]
        # preferred_label should NOT be set (suggested_next_ids takes precedence)
        assert outcome.preferred_label is None

    @pytest.mark.asyncio
    async def test_reject_returns_suggested_next_ids_for_fix(self):
        """Selecting 'Reject' maps to the fix node via suggested_next_ids."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]
        interviewer = QueueInterviewer(
            [
                Answer(
                    value="Reject", selected_option=Option(key="Reject", label="Reject")
                ),
            ]
        )
        handler = HumanGateHandler(interviewer=interviewer)
        outcome = await handler.execute(node, _make_context(), graph, "/tmp")
        assert outcome.status == StageStatus.SUCCESS
        assert outcome.suggested_next_ids == ["fix"]
        assert outcome.preferred_label is None

    @pytest.mark.asyncio
    async def test_unlabeled_edge_uses_to_node_as_choice(self):
        """Edges without labels use to_node as the choice key."""
        graph = Graph(
            name="test",
            nodes={
                "gate": Node(id="gate", shape="hexagon", label="Wait"),
                "next": Node(id="next", shape="box"),
            },
            edges=[
                Edge(from_node="gate", to_node="next"),  # no label
            ],
        )
        handler = HumanGateHandler(interviewer=AutoApproveInterviewer())
        outcome = await handler.execute(
            graph.nodes["gate"], _make_context(), graph, "/tmp"
        )
        assert outcome.status == StageStatus.SUCCESS
        assert outcome.suggested_next_ids == ["next"]


# --- L-11: Accelerator key parsing ---


class TestAcceleratorKeyParsing:
    """L-11: Edge labels like '[Y] Yes' should have accelerator keys extracted."""

    def test_bracket_key_extracted(self):
        from amplifier_module_loop_pipeline.handlers.human import _parse_accelerator_key

        assert _parse_accelerator_key("[Y] Yes") == "Y"

    def test_bracket_multi_char_key(self):
        from amplifier_module_loop_pipeline.handlers.human import _parse_accelerator_key

        assert _parse_accelerator_key("[OK] Okay") == "OK"

    def test_number_paren_key_extracted(self):
        from amplifier_module_loop_pipeline.handlers.human import _parse_accelerator_key

        assert _parse_accelerator_key("1) Option One") == "1"

    def test_number_dot_key_extracted(self):
        from amplifier_module_loop_pipeline.handlers.human import _parse_accelerator_key

        assert _parse_accelerator_key("2. Option Two") == "2"

    def test_plain_label_returns_full_label(self):
        from amplifier_module_loop_pipeline.handlers.human import _parse_accelerator_key

        assert _parse_accelerator_key("Approve") == "Approve"

    def test_empty_label_returns_empty(self):
        from amplifier_module_loop_pipeline.handlers.human import _parse_accelerator_key

        assert _parse_accelerator_key("") == ""

    @pytest.mark.asyncio
    async def test_accelerator_keys_used_in_options(self):
        """Options should use accelerator keys when present in edge labels."""
        graph = Graph(
            name="test",
            nodes={
                "gate": Node(id="gate", shape="hexagon", label="Pick one"),
                "yes": Node(id="yes", shape="box"),
                "no": Node(id="no", shape="box"),
            },
            edges=[
                Edge(from_node="gate", to_node="yes", label="[Y] Yes"),
                Edge(from_node="gate", to_node="no", label="[N] No"),
            ],
        )
        captured_questions: list[Question] = []

        def capture_callback(q: Question) -> Answer:
            captured_questions.append(q)
            # Select using the accelerator key "Y"
            return Answer(value="Y", selected_option=Option(key="Y", label="[Y] Yes"))

        handler = HumanGateHandler(interviewer=CallbackInterviewer(capture_callback))
        outcome = await handler.execute(
            graph.nodes["gate"], _make_context(), graph, "/tmp"
        )
        assert len(captured_questions) == 1
        # Options should have accelerator key as the key
        assert captured_questions[0].options[0].key == "Y"
        assert captured_questions[0].options[1].key == "N"
        assert outcome.status == StageStatus.SUCCESS
        assert outcome.suggested_next_ids == ["yes"]


# --- Handler registration ---


class TestHumanHandlerRegistration:
    """Handler registry resolves hexagon shape to HumanGateHandler."""

    def test_registry_resolves_human_handler(self):
        from amplifier_module_loop_pipeline.handlers import HandlerRegistry

        registry = HandlerRegistry(HandlerContext())
        node = Node(id="gate", shape="hexagon")
        handler = registry.get(node)
        assert isinstance(handler, HumanGateHandler)


@pytest.mark.asyncio
async def test_human_handler_emits_interview_events():
    """HumanGateHandler must emit interview lifecycle events."""
    from amplifier_module_loop_pipeline.pipeline_events import (
        PIPELINE_INTERVIEW_COMPLETED,
        PIPELINE_INTERVIEW_STARTED,
    )

    emitted: list[tuple[str, dict]] = []

    class MockHooks:
        async def emit(self, event_name, data):
            emitted.append((event_name, data))

    handler = HumanGateHandler(interviewer=AutoApproveInterviewer(), hooks=MockHooks())

    node = Node(id="gate", shape="hexagon", label="Approve?")
    graph = Graph(
        name="test",
        nodes={"gate": node, "yes": Node(id="yes", shape="box")},
        edges=[Edge(from_node="gate", to_node="yes", label="Yes")],
    )

    ctx = PipelineContext()
    await handler.execute(node, ctx, graph, "/tmp/test")

    event_names = [e[0] for e in emitted]
    assert PIPELINE_INTERVIEW_STARTED in event_names
    assert PIPELINE_INTERVIEW_COMPLETED in event_names


# --- async_ask: HumanGateHandler uses async_ask when available ---


class TestHumanGateHandlerAsyncAsk:
    """HumanGateHandler.execute() uses async_ask when interviewer exposes it.

    Verifies that the deadlock-safe code path is taken when the interviewer
    provides async_ask (e.g. InputRequestInterviewer) instead of falling back
    to the blocking ask() call that requires nest_asyncio.
    """

    @pytest.mark.asyncio
    async def test_uses_async_ask_when_available(self):
        """execute() awaits async_ask when interviewer has the method."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]

        async_ask_called = []

        class AsyncCapableInterviewer:
            """Interviewer that exposes async_ask — simulates InputRequestInterviewer."""

            def ask(self, question: Question) -> Answer:
                raise AssertionError(
                    "ask() must NOT be called when async_ask is present"
                )

            async def async_ask(self, question: Question) -> Answer:
                async_ask_called.append(question)
                return Answer(
                    value="Approve",
                    selected_option=Option(key="Approve", label="Approve"),
                )

        handler = HumanGateHandler(interviewer=AsyncCapableInterviewer())
        outcome = await handler.execute(node, _make_context(), graph, "/tmp")

        assert len(async_ask_called) == 1
        assert outcome.status == StageStatus.SUCCESS
        assert outcome.suggested_next_ids == ["deploy"]

    @pytest.mark.asyncio
    async def test_falls_back_to_ask_when_no_async_ask(self):
        """execute() calls ask() when interviewer does not have async_ask."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]

        # AutoApproveInterviewer only has ask() — no async_ask
        handler = HumanGateHandler(interviewer=AutoApproveInterviewer())
        assert not hasattr(AutoApproveInterviewer(), "async_ask")

        outcome = await handler.execute(node, _make_context(), graph, "/tmp")

        assert outcome.status == StageStatus.SUCCESS
        assert outcome.suggested_next_ids == ["deploy"]

    @pytest.mark.asyncio
    async def test_async_ask_receives_correct_question_stage(self):
        """async_ask receives a Question with stage matching the node id."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]

        captured: list[Question] = []

        class AsyncCapableInterviewer:
            def ask(self, question: Question) -> Answer:
                raise AssertionError("ask() must NOT be called")

            async def async_ask(self, question: Question) -> Answer:
                captured.append(question)
                return Answer(
                    value="Approve",
                    selected_option=Option(key="Approve", label="Approve"),
                )

        handler = HumanGateHandler(interviewer=AsyncCapableInterviewer())
        await handler.execute(node, _make_context(), graph, "/tmp")

        assert len(captured) == 1
        assert captured[0].stage == "review"
        assert captured[0].type == QuestionType.MULTIPLE_CHOICE


class TestHumanGateFreeformMode:
    """Freeform mode: mode='freeform' generates FREEFORM question, stores human.gate.text."""

    @pytest.mark.asyncio
    async def test_freeform_mode_generates_freeform_question(self):
        """mode='freeform' on hexagon node generates FREEFORM question and stores human.gate.text."""
        graph = Graph(
            name="test",
            nodes={
                "brainstorm": Node(
                    id="brainstorm",
                    shape="hexagon",
                    label="Brainstorm with Human",
                    attrs={"mode": "freeform"},
                ),
                "refine": Node(id="refine", shape="box"),
            },
            edges=[
                Edge(from_node="brainstorm", to_node="refine"),
            ],
        )

        captured_questions: list[Question] = []

        class FreeformInterviewer:
            def ask(self, question: Question) -> Answer:
                raise AssertionError(
                    "ask() must NOT be called when async_ask is present"
                )

            async def async_ask(self, question: Question) -> Answer:
                captured_questions.append(question)
                return Answer(
                    value="I think we should focus on the API",
                    text="I think we should focus on the API",
                )

        handler = HumanGateHandler(interviewer=FreeformInterviewer())
        outcome = await handler.execute(
            graph.nodes["brainstorm"], _make_context(), graph, "/tmp"
        )

        # Verify FREEFORM question was generated
        assert len(captured_questions) == 1
        assert captured_questions[0].type == QuestionType.FREEFORM
        assert captured_questions[0].text == "Brainstorm with Human"
        assert captured_questions[0].stage == "brainstorm"

        # Verify outcome
        assert outcome.status == StageStatus.SUCCESS
        assert outcome.suggested_next_ids == ["refine"]

        # Verify context_updates include human.gate.text
        assert outcome.context_updates is not None
        assert (
            outcome.context_updates["human.gate.text"]
            == "I think we should focus on the API"
        )
        assert outcome.context_updates["human.gate.label"] == "Brainstorm with Human"

    @pytest.mark.asyncio
    async def test_no_mode_attribute_still_generates_multiple_choice(self):
        """Hexagon node without mode attribute generates MULTIPLE_CHOICE (no regression)."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]

        captured_questions: list[Question] = []

        class CapturingInterviewer:
            def ask(self, question: Question) -> Answer:
                raise AssertionError(
                    "ask() must NOT be called when async_ask is present"
                )

            async def async_ask(self, question: Question) -> Answer:
                captured_questions.append(question)
                return Answer(
                    value="Approve",
                    selected_option=Option(key="Approve", label="Approve"),
                )

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        outcome = await handler.execute(node, _make_context(), graph, "/tmp")

        # Verify MULTIPLE_CHOICE question was generated (not FREEFORM)
        assert len(captured_questions) == 1
        assert captured_questions[0].type == QuestionType.MULTIPLE_CHOICE

        # Verify outcome uses standard routing
        assert outcome.status == StageStatus.SUCCESS
        assert outcome.suggested_next_ids == ["deploy"]

        # Verify context_updates do NOT include human.gate.text
        assert outcome.context_updates is not None
        assert "human.gate.text" not in outcome.context_updates
        assert "human.gate.selected" in outcome.context_updates


# ---------------------------------------------------------------------------
# TestHumanGateFreeformRichAttachments — rich input request metadata
# ---------------------------------------------------------------------------


class TestHumanGateFreeformRichAttachments:
    """Rich input request: description, attachments_inline, attachments_ref populate Question.metadata."""

    @pytest.mark.asyncio
    async def test_description_expanded_into_metadata(self, tmp_path):
        """description attribute with $variable token is expanded and stored in metadata."""
        graph = Graph(
            name="test",
            nodes={
                "review": Node(
                    id="review",
                    shape="hexagon",
                    label="Review",
                    attrs={"mode": "freeform", "description": "Review: $last_response"},
                ),
                "next": Node(id="next", shape="box"),
            },
            edges=[Edge(from_node="review", to_node="next")],
        )
        graph.source_dir = str(tmp_path)

        ctx = PipelineContext()
        ctx.update({"last_response": "The analysis is complete."})

        captured: list[Question] = []

        class CapturingInterviewer:
            def ask(self, q: Question) -> Answer:
                raise AssertionError(
                    "ask() must not be called when async_ask is present"
                )

            async def async_ask(self, q: Question) -> Answer:
                captured.append(q)
                return Answer(value="ok", text="ok")

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        outcome = await handler.execute(graph.nodes["review"], ctx, graph, "/tmp")

        assert len(captured) == 1
        q = captured[0]
        assert "description" in q.metadata
        assert "The analysis is complete." in q.metadata["description"]
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_attachments_inline_resolved_to_envelopes(self, tmp_path):
        """attachments_inline glob is resolved to file envelopes in Question.metadata."""
        ai_dir = tmp_path / ".ai"
        ai_dir.mkdir()
        (ai_dir / "analysis.md").write_text("# Analysis\n\nTest content.")

        graph = Graph(
            name="test",
            nodes={
                "review": Node(
                    id="review",
                    shape="hexagon",
                    label="Review",
                    attrs={"mode": "freeform", "attachments_inline": ".ai/analysis.md"},
                ),
                "next": Node(id="next", shape="box"),
            },
            edges=[Edge(from_node="review", to_node="next")],
        )
        graph.source_dir = str(tmp_path)

        captured: list[Question] = []

        class CapturingInterviewer:
            def ask(self, q: Question) -> Answer:
                raise AssertionError(
                    "ask() must not be called when async_ask is present"
                )

            async def async_ask(self, q: Question) -> Answer:
                captured.append(q)
                return Answer(value="ok", text="ok")

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        await handler.execute(graph.nodes["review"], _make_context(), graph, "/tmp")

        q = captured[0]
        assert "attachments_inline" in q.metadata
        assert len(q.metadata["attachments_inline"]) == 1
        env = q.metadata["attachments_inline"][0]
        assert env["filename"] == "analysis.md"
        assert env["path"] == ".ai/analysis.md"
        assert "Test content." in env["content"]
        assert env["directory"] == ".ai"

    @pytest.mark.asyncio
    async def test_glob_wildcard_resolves_multiple_ref_files(self, tmp_path):
        """Glob wildcard in attachments_ref resolves all matching files."""
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.md").write_text("File A")
        (docs / "b.md").write_text("File B")
        (docs / "c.txt").write_text("Not a markdown file")

        graph = Graph(
            name="test",
            nodes={
                "review": Node(
                    id="review",
                    shape="hexagon",
                    label="Review",
                    attrs={"mode": "freeform", "attachments_ref": "docs/*.md"},
                ),
                "next": Node(id="next", shape="box"),
            },
            edges=[Edge(from_node="review", to_node="next")],
        )
        graph.source_dir = str(tmp_path)

        captured: list[Question] = []

        class CapturingInterviewer:
            def ask(self, q: Question) -> Answer:
                raise AssertionError(
                    "ask() must not be called when async_ask is present"
                )

            async def async_ask(self, q: Question) -> Answer:
                captured.append(q)
                return Answer(value="ok", text="ok")

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        await handler.execute(graph.nodes["review"], _make_context(), graph, "/tmp")

        q = captured[0]
        assert "attachments_ref" in q.metadata
        assert len(q.metadata["attachments_ref"]) == 2
        filenames = {e["filename"] for e in q.metadata["attachments_ref"]}
        assert filenames == {"a.md", "b.md"}

    @pytest.mark.asyncio
    async def test_recursive_glob_resolves_subdirectories(self, tmp_path):
        """Recursive ** glob finds files in nested subdirectories."""
        ai = tmp_path / ".ai"
        ai.mkdir()
        sub = ai / "sub"
        sub.mkdir()
        (ai / "top.md").write_text("Top level")
        (sub / "nested.md").write_text("Nested content")

        graph = Graph(
            name="test",
            nodes={
                "review": Node(
                    id="review",
                    shape="hexagon",
                    label="Review",
                    attrs={"mode": "freeform", "attachments_ref": ".ai/**/*.md"},
                ),
                "next": Node(id="next", shape="box"),
            },
            edges=[Edge(from_node="review", to_node="next")],
        )
        graph.source_dir = str(tmp_path)

        captured: list[Question] = []

        class CapturingInterviewer:
            def ask(self, q: Question) -> Answer:
                raise AssertionError(
                    "ask() must not be called when async_ask is present"
                )

            async def async_ask(self, q: Question) -> Answer:
                captured.append(q)
                return Answer(value="ok", text="ok")

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        await handler.execute(graph.nodes["review"], _make_context(), graph, "/tmp")

        q = captured[0]
        assert "attachments_ref" in q.metadata
        filenames = {e["filename"] for e in q.metadata["attachments_ref"]}
        assert "top.md" in filenames
        assert "nested.md" in filenames

    @pytest.mark.asyncio
    async def test_no_matching_files_gives_no_attachment_key(self, tmp_path):
        """When glob matches nothing, attachment key is absent from metadata."""
        graph = Graph(
            name="test",
            nodes={
                "review": Node(
                    id="review",
                    shape="hexagon",
                    label="Review",
                    attrs={
                        "mode": "freeform",
                        "attachments_inline": "nonexistent/*.md",
                    },
                ),
                "next": Node(id="next", shape="box"),
            },
            edges=[Edge(from_node="review", to_node="next")],
        )
        graph.source_dir = str(tmp_path)

        captured: list[Question] = []

        class CapturingInterviewer:
            def ask(self, q: Question) -> Answer:
                raise AssertionError(
                    "ask() must not be called when async_ask is present"
                )

            async def async_ask(self, q: Question) -> Answer:
                captured.append(q)
                return Answer(value="ok", text="ok")

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        outcome = await handler.execute(
            graph.nodes["review"], _make_context(), graph, "/tmp"
        )

        q = captured[0]
        assert "attachments_inline" not in q.metadata
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_node_without_new_attrs_has_empty_metadata(self, tmp_path):
        """Hexagon freeform node without description/attachments has empty metadata (backward compat)."""
        graph = Graph(
            name="test",
            nodes={
                "review": Node(
                    id="review",
                    shape="hexagon",
                    label="Review",
                    attrs={"mode": "freeform"},
                ),
                "next": Node(id="next", shape="box"),
            },
            edges=[Edge(from_node="review", to_node="next")],
        )
        graph.source_dir = str(tmp_path)

        captured: list[Question] = []

        class CapturingInterviewer:
            def ask(self, q: Question) -> Answer:
                raise AssertionError(
                    "ask() must not be called when async_ask is present"
                )

            async def async_ask(self, q: Question) -> Answer:
                captured.append(q)
                return Answer(value="ok", text="ok")

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        outcome = await handler.execute(
            graph.nodes["review"], _make_context(), graph, "/tmp"
        )

        q = captured[0]
        assert "description" not in q.metadata
        assert "attachments_inline" not in q.metadata
        assert "attachments_ref" not in q.metadata
        assert outcome.status == StageStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_all_three_attrs_together(self, tmp_path):
        """description + attachments_inline + attachments_ref all appear in metadata."""
        ai_dir = tmp_path / ".ai"
        ai_dir.mkdir()
        (ai_dir / "analysis.md").write_text("# Analysis\n\nResults here.")
        (ai_dir / "backlog.md").write_text("# Backlog\n\n- Item 1")
        (ai_dir / "steering.md").write_text("# Steering\n\nDirection notes.")

        graph = Graph(
            name="test",
            nodes={
                "review": Node(
                    id="review",
                    shape="hexagon",
                    label="Review Analysis",
                    attrs={
                        "mode": "freeform",
                        "description": "Please review. Context: $last_response",
                        "attachments_inline": ".ai/analysis.md",
                        "attachments_ref": ".ai/backlog.md,.ai/steering.md",
                    },
                ),
                "next": Node(id="next", shape="box"),
            },
            edges=[Edge(from_node="review", to_node="next")],
        )
        graph.source_dir = str(tmp_path)

        ctx = PipelineContext()
        ctx.update({"last_response": "step done"})

        captured: list[Question] = []

        class CapturingInterviewer:
            def ask(self, q: Question) -> Answer:
                raise AssertionError(
                    "ask() must not be called when async_ask is present"
                )

            async def async_ask(self, q: Question) -> Answer:
                captured.append(q)
                return Answer(value="lgtm", text="lgtm")

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        outcome = await handler.execute(graph.nodes["review"], ctx, graph, "/tmp")

        q = captured[0]
        assert "step done" in q.metadata["description"]
        assert len(q.metadata["attachments_inline"]) == 1
        assert q.metadata["attachments_inline"][0]["filename"] == "analysis.md"
        assert len(q.metadata["attachments_ref"]) == 2
        ref_names = {e["filename"] for e in q.metadata["attachments_ref"]}
        assert ref_names == {"backlog.md", "steering.md"}
        assert outcome.status == StageStatus.SUCCESS
        assert outcome.context_updates["human.gate.text"] == "lgtm"


# --- last_response propagation (so $context works in downstream nodes) ---


class TestHumanGateLastResponsePropagation:
    """Human gate must set last_response so $context works in subsequent nodes.

    When HumanBrainstorm -> RefineUnderstanding, the prompt in RefineUnderstanding
    uses $context which resolves to context['last_response'].  If human gate handlers
    don't set last_response the LLM sees an empty string and says "I don't see your
    answer."  Both code paths (freeform and choice-picker) must set it.
    """

    @pytest.mark.asyncio
    async def test_freeform_gate_sets_last_response(self):
        """Freeform gate sets last_response so $context works in the next node."""
        graph = Graph(
            name="test",
            nodes={
                "HumanBrainstorm": Node(
                    id="HumanBrainstorm",
                    shape="hexagon",
                    label="What are we building?",
                    attrs={"mode": "freeform"},
                ),
                "RefineUnderstanding": Node(id="RefineUnderstanding", shape="box"),
            },
            edges=[
                Edge(from_node="HumanBrainstorm", to_node="RefineUnderstanding"),
            ],
        )

        class FreeformInterviewer:
            def ask(self, question: Question) -> Answer:
                raise AssertionError(
                    "ask() must NOT be called when async_ask is present"
                )

            async def async_ask(self, question: Question) -> Answer:
                return Answer(
                    value="B - Command-line tool",
                    text="B - Command-line tool",
                )

        handler = HumanGateHandler(interviewer=FreeformInterviewer())
        outcome = await handler.execute(
            graph.nodes["HumanBrainstorm"], _make_context(), graph, "/tmp"
        )

        assert outcome.status == StageStatus.SUCCESS
        assert outcome.context_updates is not None
        # Core check: last_response must be set so $context works downstream
        assert "last_response" in outcome.context_updates, (
            "Freeform gate must set 'last_response' so $context works in subsequent nodes"
        )
        assert outcome.context_updates["last_response"] == "B - Command-line tool"[:200]
        # last_stage should be set to match codergen convention
        assert "last_stage" in outcome.context_updates
        assert outcome.context_updates["last_stage"] == "HumanBrainstorm"
        # Existing keys still present (no regression)
        assert outcome.context_updates["human.gate.text"] == "B - Command-line tool"

    @pytest.mark.asyncio
    async def test_freeform_gate_truncates_long_response_for_last_response(self):
        """last_response is truncated to 200 chars to match codergen convention."""
        graph = Graph(
            name="test",
            nodes={
                "gate": Node(
                    id="gate",
                    shape="hexagon",
                    label="Tell me everything",
                    attrs={"mode": "freeform"},
                ),
                "next": Node(id="next", shape="box"),
            },
            edges=[Edge(from_node="gate", to_node="next")],
        )

        long_text = "A" * 500

        class LongResponseInterviewer:
            def ask(self, question: Question) -> Answer:
                raise AssertionError("ask() must NOT be called")

            async def async_ask(self, question: Question) -> Answer:
                return Answer(value=long_text, text=long_text)

        handler = HumanGateHandler(interviewer=LongResponseInterviewer())
        outcome = await handler.execute(
            graph.nodes["gate"], _make_context(), graph, "/tmp"
        )

        assert outcome.context_updates is not None
        assert len(outcome.context_updates["last_response"]) == 200
        assert outcome.context_updates["last_response"] == long_text[:200]
        # human.gate.text gets full text (not truncated)
        assert outcome.context_updates["human.gate.text"] == long_text

    @pytest.mark.asyncio
    async def test_choice_gate_sets_last_response(self):
        """Choice-picker gate sets last_response so $context works in the next node."""
        graph = _make_graph_with_human_gate()
        node = graph.nodes["review"]

        interviewer = QueueInterviewer(
            [
                Answer(
                    value="Approve",
                    selected_option=Option(key="Approve", label="Approve"),
                )
            ]
        )
        handler = HumanGateHandler(interviewer=interviewer)
        outcome = await handler.execute(node, _make_context(), graph, "/tmp")

        assert outcome.status == StageStatus.SUCCESS
        assert outcome.context_updates is not None
        # Core check: last_response must be set so $context works downstream
        assert "last_response" in outcome.context_updates, (
            "Choice-picker gate must set 'last_response' so $context works in subsequent nodes"
        )
        assert outcome.context_updates["last_response"] == "Approve"
        assert "last_stage" in outcome.context_updates
        assert outcome.context_updates["last_stage"] == "review"
        # Existing keys still present (no regression)
        assert outcome.context_updates["human.gate.selected"] == "Approve"


class TestHumanGatePromptSubstitution:
    """HUMAN-SUB-001: prompt=$variable is expanded via context before presenting to human.

    The DOT parser stores the ``prompt`` attribute as a first-class ``node.prompt``
    field (it pops "prompt" from attrs).  The handler must read ``node.prompt`` and
    expand any ``$variable`` tokens before building the Question — otherwise the user
    sees the raw token (or, because ``node.attrs.get("prompt")`` returns None, the
    fallback ``node.label``).

    Issue 14 regression test.
    """

    @pytest.mark.asyncio
    async def test_node_prompt_field_substituted_with_context_variable(self):
        """Choice-gate: prompt stored in node.prompt (not attrs) is expanded.

        Simulates how the DOT parser creates nodes: ``prompt`` is a first-class
        field, NOT inside ``attrs``.  The handler must use ``node.prompt`` and
        expand ``$gate_topic`` from context.
        """
        gate_question = (
            "GATE 1: DECOMPOSABILITY\n\nCan the problem be broken into units?"
        )

        graph = Graph(
            name="conversational_gate",
            nodes={
                "ask": Node(
                    id="ask",
                    shape="hexagon",
                    label="Ask Human",
                    # DOT-parsed nodes have prompt as first-class field, NOT in attrs
                    prompt="$gate_topic",
                    attrs={},  # attrs does NOT have "prompt"
                ),
                "eval": Node(id="eval", shape="box"),
            },
            edges=[Edge(from_node="ask", to_node="eval", label="continue")],
        )

        context = PipelineContext()
        context.set("gate_topic", gate_question)

        captured_questions: list[Question] = []

        class CapturingInterviewer:
            def ask(self, question: Question) -> Answer:
                raise AssertionError("ask() must NOT be called; use async_ask")

            def ask_multiple(self, questions: list[Question]) -> list[Answer]:
                raise AssertionError("ask_multiple() must NOT be called")

            def inform(self, message: str) -> None:
                pass

            async def async_ask(self, question: Question) -> Answer:
                captured_questions.append(question)
                return Answer(
                    value="continue",
                    selected_option=Option(key="continue", label="continue"),
                )

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        await handler.execute(graph.nodes["ask"], context, graph, "/tmp")

        assert len(captured_questions) == 1
        captured = captured_questions[0]
        # The prompt MUST be the expanded gate_topic, NOT "Ask Human" (the label)
        # and NOT the literal "$gate_topic" token.
        assert captured.text == gate_question, (
            f"Expected expanded gate_topic in prompt, got: {captured.text!r}"
        )

    @pytest.mark.asyncio
    async def test_freeform_node_prompt_field_substituted_with_context_variable(self):
        """Freeform gate: prompt stored in node.prompt (not attrs) is expanded.

        Same regression as above but for the freeform (mode=freeform) path.
        """
        gate_question = (
            "GATE 1: DECOMPOSABILITY\n\nDescribe your project's decomposability."
        )

        graph = Graph(
            name="conversational_gate",
            nodes={
                "ask": Node(
                    id="ask",
                    shape="hexagon",
                    label="Ask Human",
                    prompt="$gate_topic",
                    attrs={"mode": "freeform"},
                ),
                "eval": Node(id="eval", shape="box"),
            },
            edges=[Edge(from_node="ask", to_node="eval")],
        )

        context = PipelineContext()
        context.set("gate_topic", gate_question)

        captured_questions: list[Question] = []

        class CapturingInterviewer:
            def ask(self, question: Question) -> Answer:
                raise AssertionError("ask() must NOT be called; use async_ask")

            def ask_multiple(self, questions: list[Question]) -> list[Answer]:
                raise AssertionError("ask_multiple() must NOT be called")

            def inform(self, message: str) -> None:
                pass

            async def async_ask(self, question: Question) -> Answer:
                captured_questions.append(question)
                return Answer(value="user typed some text", text="user typed some text")

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        await handler.execute(graph.nodes["ask"], context, graph, "/tmp")

        assert len(captured_questions) == 1
        captured = captured_questions[0]
        assert captured.text == gate_question, (
            f"Expected expanded gate_topic in freeform prompt, got: {captured.text!r}"
        )

    @pytest.mark.asyncio
    async def test_prompt_in_attrs_still_works_for_legacy_nodes(self):
        """Regression: nodes with prompt in attrs (not node.prompt) still work.

        Legacy callers and tests that construct nodes with ``attrs={"prompt": ...}``
        (rather than ``prompt=`` as a first-class field) must continue to work.
        """
        explicit_prompt = "Do you approve this code?"

        graph = Graph(
            name="test",
            nodes={
                "gate": Node(
                    id="gate",
                    shape="hexagon",
                    label="Approve changes?",
                    # Legacy construction: prompt inside attrs, not node.prompt
                    attrs={"prompt": explicit_prompt},
                ),
                "next": Node(id="next", shape="box"),
            },
            edges=[Edge(from_node="gate", to_node="next", label="ok")],
        )
        context = PipelineContext()

        captured_questions: list[Question] = []

        class CapturingInterviewer:
            def ask(self, question: Question) -> Answer:
                raise AssertionError("ask() must NOT be called; use async_ask")

            def ask_multiple(self, questions: list[Question]) -> list[Answer]:
                raise AssertionError("ask_multiple() must NOT be called")

            def inform(self, message: str) -> None:
                pass

            async def async_ask(self, question: Question) -> Answer:
                captured_questions.append(question)
                return Answer(value="ok", selected_option=Option(key="ok", label="ok"))

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        await handler.execute(graph.nodes["gate"], context, graph, "/tmp")

        assert len(captured_questions) == 1
        assert captured_questions[0].text == explicit_prompt, (
            f"Expected attrs-based prompt, got: {captured_questions[0].text!r}"
        )


class TestGateFeedbackPromptExpansion:
    """Issue 17 redux: gate_feedback context variable is expanded in freeform ask prompt.

    When the eval node decides need_more and writes gate_feedback to context via
    context_updates, the ask prompt on the next turn must show that feedback so the
    user knows WHY they are being asked again and WHAT more is needed.

    Acceptance criteria from issue 17 redux:
    - Turn 1 with gate_feedback="" → prompt shows empty string (no stale text)
    - Turn 2 with gate_feedback set → prompt includes the eval's feedback text
    - Turn counter (${_gate_iter.ask}) appears at the start of the prompt
    """

    @pytest.mark.asyncio
    async def test_gate_feedback_empty_string_expands_cleanly_on_turn_1(self):
        """gate_feedback="" (set by init_feedback node) is invisible on turn 1.

        When gate_feedback is in context as an empty string, the ask prompt
        (turn ${_gate_iter.ask}) $gate_topic\n\n$gate_feedback renders as just
        the turn counter + gate_topic, with no trailing noise.
        """
        gate_question = "GATE 1: DECOMPOSABILITY\n\nDescribe your project."

        graph = Graph(
            name="conversational_gate",
            nodes={
                "ask": Node(
                    id="ask",
                    shape="hexagon",
                    label="Ask Human",
                    prompt="(turn ${_gate_iter.ask}) $gate_topic\n\n$gate_feedback",
                    attrs={"mode": "freeform"},
                ),
                "eval": Node(id="eval", shape="box"),
            },
            edges=[Edge(from_node="ask", to_node="eval")],
        )

        context = PipelineContext()
        context.set("gate_topic", gate_question)
        context.set("gate_feedback", "")  # simulates init_feedback tool node

        captured_questions: list[Question] = []

        class CapturingInterviewer:
            def ask(self, question: Question) -> Answer:
                raise AssertionError("ask() must NOT be called; use async_ask")

            def ask_multiple(self, questions: list[Question]) -> list[Answer]:
                raise AssertionError("ask_multiple() must NOT be called")

            def inform(self, message: str) -> None:
                pass

            async def async_ask(self, question: Question) -> Answer:
                captured_questions.append(question)
                return Answer(value="first response", text="first response")

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        await handler.execute(graph.nodes["ask"], context, graph, "/tmp")

        assert len(captured_questions) == 1
        prompt = captured_questions[0].text
        assert "(turn 1)" in prompt, f"Expected '(turn 1)' in prompt, got: {prompt!r}"
        assert gate_question in prompt, "Expected gate_topic in prompt"
        # empty gate_feedback → no stale feedback text in prompt
        assert prompt == f"(turn 1) {gate_question}\n\n", (
            f"Expected clean turn-1 prompt, got: {prompt!r}"
        )

    @pytest.mark.asyncio
    async def test_gate_feedback_set_by_eval_appears_on_turn_2(self):
        """gate_feedback from eval is shown on the second ask iteration.

        After eval decides need_more and writes gate_feedback to context, the
        ask prompt on the next call must include that feedback.  This gives the
        user a clear explanation of WHY they are being asked again.
        """
        gate_question = "GATE 1: DECOMPOSABILITY\n\nDescribe your project."
        eval_feedback = (
            "Please provide a specific score (0-100) and list at least 5 concrete "
            "features. Your response did not include numeric evidence."
        )

        graph = Graph(
            name="conversational_gate",
            nodes={
                "ask": Node(
                    id="ask",
                    shape="hexagon",
                    label="Ask Human",
                    prompt="(turn ${_gate_iter.ask}) $gate_topic\n\n$gate_feedback",
                    attrs={"mode": "freeform"},
                ),
                "eval": Node(id="eval", shape="box"),
            },
            edges=[Edge(from_node="ask", to_node="eval")],
        )

        context = PipelineContext()
        context.set("gate_topic", gate_question)
        context.set("gate_feedback", "")  # turn 1: cleared by init_feedback

        captured_questions: list[Question] = []

        class CapturingInterviewer:
            def ask(self, question: Question) -> Answer:
                raise AssertionError("ask() must NOT be called; use async_ask")

            def ask_multiple(self, questions: list[Question]) -> list[Answer]:
                raise AssertionError("ask_multiple() must NOT be called")

            def inform(self, message: str) -> None:
                pass

            async def async_ask(self, question: Question) -> Answer:
                captured_questions.append(question)
                return Answer(value="brief response", text="brief response")

        handler = HumanGateHandler(interviewer=CapturingInterviewer())

        # Turn 1
        await handler.execute(graph.nodes["ask"], context, graph, "/tmp")

        # Simulate eval deciding need_more and writing gate_feedback
        context.set("gate_feedback", eval_feedback)

        # Turn 2 — same node fired again on loop-back
        await handler.execute(graph.nodes["ask"], context, graph, "/tmp")

        assert len(captured_questions) == 2

        turn1_prompt = captured_questions[0].text
        turn2_prompt = captured_questions[1].text

        # Turn 1: no feedback, just gate topic
        assert "(turn 1)" in turn1_prompt, (
            f"Turn 1 missing turn counter: {turn1_prompt!r}"
        )
        assert eval_feedback not in turn1_prompt, "Turn 1 must not show eval feedback"

        # Turn 2: turn counter shows progress, feedback explains what's needed
        assert "(turn 2)" in turn2_prompt, (
            f"Turn 2 missing turn counter: {turn2_prompt!r}"
        )
        assert eval_feedback in turn2_prompt, (
            f"Turn 2 must include eval feedback, got: {turn2_prompt!r}"
        )

    @pytest.mark.asyncio
    async def test_turn_counter_at_start_of_prompt_is_visible(self):
        """(turn N) at START of prompt makes it visible even for long gate_topics.

        The Issue 17 redux user complaint was '(turn 2)' at the END of a very long
        prompt — easy to miss.  The fixed prompt places it at the START so it is
        the first thing the user reads.
        """
        gate_question = "A" * 500  # simulates a long gate topic

        graph = Graph(
            name="conversational_gate",
            nodes={
                "ask": Node(
                    id="ask",
                    shape="hexagon",
                    label="Ask Human",
                    prompt="(turn ${_gate_iter.ask}) $gate_topic\n\n$gate_feedback",
                    attrs={"mode": "freeform"},
                ),
                "eval": Node(id="eval", shape="box"),
            },
            edges=[Edge(from_node="ask", to_node="eval")],
        )

        context = PipelineContext()
        context.set("gate_topic", gate_question)
        context.set("gate_feedback", "")

        captured_questions: list[Question] = []

        class CapturingInterviewer:
            def ask(self, question: Question) -> Answer:
                raise AssertionError("ask() must NOT be called; use async_ask")

            def ask_multiple(self, questions: list[Question]) -> list[Answer]:
                raise AssertionError("ask_multiple() must NOT be called")

            def inform(self, message: str) -> None:
                pass

            async def async_ask(self, question: Question) -> Answer:
                captured_questions.append(question)
                return Answer(value="response", text="response")

        handler = HumanGateHandler(interviewer=CapturingInterviewer())
        await handler.execute(graph.nodes["ask"], context, graph, "/tmp")

        prompt = captured_questions[0].text
        # '(turn 1)' must appear in the first 12 characters
        assert prompt.startswith("(turn 1)"), (
            f"Turn counter must be at the START of prompt, got: {prompt[:40]!r}"
        )
