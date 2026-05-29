"""Tests for P7: context variable injection from parent nodes into child pipelines.

When a folder node (PipelineHandler) or house node (ManagerLoopHandler) has
attributes like context.my_var="hello", the child pipeline should be able to
reference $my_var in its prompts.

Task 6 baselines replaced by Task 8 post-fix tests.

Tests:
- TestContextInjectionPipelineHandler: PipelineHandler (folder/pipeline nodes)
- TestContextInjectionManagerLoop: ManagerLoopHandler (house nodes)
"""

from __future__ import annotations

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


# ---------------------------------------------------------------------------
# CapturingBackend
# ---------------------------------------------------------------------------


class CapturingBackend:
    """Records node_id → prompt text and node_id → context snapshot."""

    def __init__(self) -> None:
        self.prompts: dict[str, str] = {}
        self.context_snapshots: dict[str, dict] = {}

    async def run(self, node: Node, prompt: str, context: PipelineContext) -> str:
        self.prompts[node.id] = prompt
        self.context_snapshots[node.id] = context.snapshot()
        return "done"

    def prompt_for(self, node_id: str) -> str:
        """Return the captured prompt for a node, or '' if not captured."""
        return self.prompts.get(node_id, "")

    def context_for(self, node_id: str) -> dict:
        """Return the captured context snapshot for a node, or {} if not captured."""
        return self.context_snapshots.get(node_id, {})


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_dot(path: str, content: str) -> None:
    """Write DOT content to a file at the given path."""
    with open(path, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# TestContextInjectionPipelineHandler
# ---------------------------------------------------------------------------


class TestContextInjectionPipelineHandler:
    """Tests for context injection via folder (pipeline) nodes."""

    @pytest.mark.asyncio
    async def test_context_attr_injected_into_child(self, tmp_path):
        """context.topic attr on folder node IS injected and $topic expands in child.

        After the fix, the folder node's context.topic="auth service" attribute
        causes $topic to be expanded in the child pipeline's prompts.
        Assert "auth service" IS in the child prompt (not $topic).
        """
        child_dot = """\
digraph child {
    start [shape=Mdiamond]
    work [prompt="Review the $topic architecture"]
    done [shape=Msquare]
    start -> work -> done
}
"""
        child_path = tmp_path / "child.dot"
        _write_dot(str(child_path), child_dot)

        parent_dot = f"""\
digraph parent {{
    start [shape=Mdiamond]
    sub [shape=folder, dot_file="{child_path}", context.topic="auth service"]
    done [shape=Msquare]
    start -> sub -> done
}}
"""
        graph = parse_dot(parent_dot)
        graph.source_dir = str(tmp_path)

        capturing = CapturingBackend()
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=capturing))
        logs_root = str(tmp_path / "logs")

        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=logs_root,
        )
        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS
        work_prompt = capturing.prompt_for("work")
        assert "auth service" in work_prompt, (
            f"Expected 'auth service' in child prompt after fix, got: {work_prompt!r}"
        )
        assert "$topic" not in work_prompt, (
            f"Expected $topic to be expanded (not raw), got: {work_prompt!r}"
        )

    @pytest.mark.asyncio
    async def test_multiple_context_attrs_all_injected(self, tmp_path):
        """Multiple context.* attrs all get injected and expanded in child.

        Parent sets context.topic, context.criteria, context.output_path.
        All three should appear expanded in the child prompt.
        """
        child_dot = """\
digraph child {
    start [shape=Mdiamond]
    work [prompt="Review $topic against $criteria and write to $output_path"]
    done [shape=Msquare]
    start -> work -> done
}
"""
        child_path = tmp_path / "child.dot"
        _write_dot(str(child_path), child_dot)

        parent_dot = f"""\
digraph parent {{
    start [shape=Mdiamond]
    sub [shape=folder, dot_file="{child_path}", context.topic="auth module", context.criteria="security requirements", context.output_path="/tmp/review.md"]
    done [shape=Msquare]
    start -> sub -> done
}}
"""
        graph = parse_dot(parent_dot)
        graph.source_dir = str(tmp_path)

        capturing = CapturingBackend()
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=capturing))
        logs_root = str(tmp_path / "logs")

        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=logs_root,
        )
        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS
        work_prompt = capturing.prompt_for("work")
        assert "auth module" in work_prompt, (
            f"Expected 'auth module' in child prompt, got: {work_prompt!r}"
        )
        assert "security requirements" in work_prompt, (
            f"Expected 'security requirements' in child prompt, got: {work_prompt!r}"
        )
        assert "/tmp/review.md" in work_prompt, (
            f"Expected '/tmp/review.md' in child prompt, got: {work_prompt!r}"
        )

    @pytest.mark.asyncio
    async def test_context_attrs_do_not_pollute_parent_context(self, tmp_path):
        """Injected context.* keys stay in child only — parent is NOT polluted.

        After running a folder node with context.topic="auth service",
        the parent PipelineContext should NOT have "topic" as a key.
        """
        child_dot = """\
digraph child {
    start [shape=Mdiamond]
    work [prompt="Review $topic"]
    done [shape=Msquare]
    start -> work -> done
}
"""
        child_path = tmp_path / "child.dot"
        _write_dot(str(child_path), child_dot)

        parent_dot = f"""\
digraph parent {{
    start [shape=Mdiamond]
    sub [shape=folder, dot_file="{child_path}", context.topic="auth service"]
    done [shape=Msquare]
    start -> sub -> done
}}
"""
        graph = parse_dot(parent_dot)
        graph.source_dir = str(tmp_path)

        capturing = CapturingBackend()
        parent_context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=capturing))
        logs_root = str(tmp_path / "logs")

        engine = PipelineEngine(
            graph=graph,
            context=parent_context,
            handler_registry=registry,
            logs_root=logs_root,
        )
        outcome = await engine.run()

        assert outcome.status == StageStatus.SUCCESS

        # Parent context must NOT have "topic" injected into it
        assert parent_context.get("topic") is None, (
            f"Expected 'topic' NOT in parent context after child run, "
            f"but got: {parent_context.get('topic')!r}"
        )

    @pytest.mark.asyncio
    async def test_parent_context_key_available_in_child_before_fix(self, tmp_path):
        """graph.goal from parent IS available in child (this works via clone).

        Baseline confirming that context.clone() already propagates graph.goal.
        The child prompt $goal should expand to the parent's goal text.
        This works WITHOUT any fix (via clone).
        """
        child_dot = """\
digraph child {
    start [shape=Mdiamond]
    work [prompt="Implement $goal system"]
    done [shape=Msquare]
    start -> work -> done
}
"""
        child_path = tmp_path / "child.dot"
        _write_dot(str(child_path), child_dot)

        parent_dot = f"""\
digraph parent {{
    goal = "user authentication"
    start [shape=Mdiamond]
    sub [shape=folder, dot_file="{child_path}"]
    done [shape=Msquare]
    start -> sub -> done
}}
"""
        graph = parse_dot(parent_dot)
        graph.source_dir = str(tmp_path)

        capturing = CapturingBackend()
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=capturing))
        logs_root = str(tmp_path / "logs")

        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=logs_root,
        )
        outcome = await engine.run(goal="user authentication")

        assert outcome.status == StageStatus.SUCCESS
        work_prompt = capturing.prompt_for("work")

        # BASELINE: graph.goal IS available in child via context clone
        assert "user authentication" in work_prompt, (
            f"Expected parent's goal 'user authentication' in child prompt "
            f"(this works via clone), got: {work_prompt!r}"
        )


# ---------------------------------------------------------------------------
# TestContextInjectionManagerLoop
# ---------------------------------------------------------------------------


class TestContextInjectionManagerLoop:
    """Tests for context injection via house (manager loop) nodes."""

    @pytest.mark.asyncio
    async def test_manager_context_attr_injected(self, tmp_path):
        """house node with context.task attr injects $task into child prompt.

        The manager node has context.task="build auth module". The child pipeline's
        prompt references $task. After the fix, $task should expand to
        "build auth module" in the child's prompt.
        """
        child_dot = """\
digraph child {
    start [shape=Mdiamond]
    work [prompt="Execute: $task"]
    done [shape=Msquare]
    start -> work -> done
}
"""
        child_path = tmp_path / "child.dot"
        _write_dot(str(child_path), child_dot)

        parent_dot = f"""\
digraph parent {{
    start [shape=Mdiamond]
    mgr [shape=house, stack.child_dotfile="{child_path}", manager.max_cycles=1, context.task="build auth module"]
    done [shape=Msquare]
    start -> mgr -> done
}}
"""
        graph = parse_dot(parent_dot)
        graph.source_dir = str(tmp_path)

        capturing = CapturingBackend()
        context = PipelineContext()
        registry = HandlerRegistry(HandlerContext(backend=capturing))
        logs_root = str(tmp_path / "logs")

        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=logs_root,
        )
        outcome = await engine.run()

        # The child should succeed (work node returns "done")
        assert outcome.status == StageStatus.SUCCESS

        work_prompt = capturing.prompt_for("work")
        assert "build auth module" in work_prompt, (
            f"Expected 'build auth module' in child prompt after manager fix, "
            f"got: {work_prompt!r}"
        )
        assert "$task" not in work_prompt, (
            f"Expected $task to be expanded (not raw), got: {work_prompt!r}"
        )
