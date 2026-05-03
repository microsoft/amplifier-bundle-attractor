"""Regression tests for SC-1: substitution count on happy-path pipelines.

R12 WS-6 — engine node-failure propagation.

SC-1 (COE Phase 4): Happy-path runs must produce at least 1 successful
${node.field} substitution, and ZERO unresolved-literal ${...} tokens
reaching bash exec.

Uses placeholder pipelines in tests/fixtures/ (no production pipeline names
in engine source — awareness rule honoured).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.validation import validate_or_raise


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class EventCapture:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append({"name": event_name, "data": data})


def _make_engine(dot_source: str, logs_root: str, hooks: Any = None) -> PipelineEngine:
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry()
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
        hooks=hooks,
    )


@pytest.mark.asyncio
async def test_fixture_pipeline_substitution_count_regression(tmp_path):
    """SC-1: fixture pipeline produces >= 1 dotted-key substitution; zero unresolved.

    Uses the synthetic placeholder pipeline in tests/fixtures/
    (no production pipeline names in engine source).
    """
    output_file = tmp_path / "substitution_result.txt"

    # Synthetic pipeline: step_a produces step.result; step_b uses ${step.result}
    dot_source = f"""
    digraph {{
        start [shape=Mdiamond]
        step_a [shape=parallelogram,
                tool_command="echo step_a_value",
                outputs="step.result"]
        step_b [shape=parallelogram,
                tool_command="echo got=${{step.result}} > {output_file}"]
        exit [shape=Msquare]
        start -> step_a -> step_b -> exit
    }}
    """
    engine = _make_engine(dot_source, logs_root=str(tmp_path))

    # Pre-populate what step_a produces (simulate its output)
    engine.context.set("step.result", "step_a_value")

    await engine.run()

    assert engine.node_outcomes["step_a"].status == StageStatus.SUCCESS
    assert engine.node_outcomes["step_b"].status == StageStatus.SUCCESS

    # Check output for successful substitution
    assert output_file.exists(), "step_b should have written to the output file"
    content = output_file.read_text()

    # Exactly 1 ${step.result} substitution should have happened
    assert "step_a_value" in content, (
        f"Expected ${'{step.result}'} to be substituted with 'step_a_value', "
        f"got: {content!r}"
    )

    # Zero unresolved literal ${...} tokens
    assert "${" not in content, (
        f"Unresolved literal ${{...}} found in output: {content!r}"
    )


@pytest.mark.asyncio
async def test_multi_step_pipeline_all_substitutions_resolve(tmp_path):
    """SC-1: Multi-step pipeline with multiple dotted-key references all resolve.

    Simulates a pipeline with 3 steps, each producing output used by the next.
    """
    output_file_1 = tmp_path / "out1.txt"
    output_file_2 = tmp_path / "out2.txt"

    dot_source = f"""
    digraph {{
        start [shape=Mdiamond]
        step_one [shape=parallelogram,
                  tool_command="echo first_output",
                  outputs="step.one.result"]
        step_two [shape=parallelogram,
                  tool_command="echo got_${{step.one.result}} > {output_file_1}",
                  outputs="step.two.result"]
        step_three [shape=parallelogram,
                    tool_command="echo final_${{step.two.result}} > {output_file_2}"]
        exit [shape=Msquare]
        start -> step_one -> step_two -> step_three -> exit
    }}
    """
    engine = _make_engine(dot_source, logs_root=str(tmp_path))

    # Pre-populate context with chain values
    engine.context.set("step.one.result", "first_output")
    engine.context.set("step.two.result", "second_output")

    await engine.run()

    # All steps should succeed
    for step in ("step_one", "step_two", "step_three"):
        assert engine.node_outcomes[step].status == StageStatus.SUCCESS, (
            f"{step} should succeed, got {engine.node_outcomes[step]}"
        )

    # No nodes should be skipped
    assert len(engine.failed_outputs) == 0


@pytest.mark.asyncio
async def test_happy_path_zero_skipped_nodes(tmp_path):
    """SC-1: On happy path, zero nodes are skipped (failed_outputs stays empty)."""
    dot_source = """
    digraph {
        start [shape=Mdiamond]
        node_a [shape=parallelogram, tool_command="echo a", outputs="a.out"]
        node_b [shape=parallelogram, tool_command="echo b", outputs="b.out"]
        node_c [shape=parallelogram, tool_command="echo c"]
        exit [shape=Msquare]
        start -> node_a -> node_b -> node_c -> exit
    }
    """
    engine = _make_engine(dot_source, logs_root=str(tmp_path))

    await engine.run()

    # No failures → failed_outputs should be empty
    assert len(engine.failed_outputs) == 0, (
        f"happy path should have empty failed_outputs, got {engine.failed_outputs}"
    )

    # All nodes succeed
    for node_id in ("node_a", "node_b", "node_c"):
        assert engine.node_outcomes[node_id].status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_dotted_key_brace_form_substitution(tmp_path):
    """Design assertion #3: ${a.b.c} resolves via braced form in tool_command."""
    output_file = tmp_path / "dotted.txt"
    engine = _make_engine(
        f"""
        digraph {{
            start [shape=Mdiamond]
            worker [shape=parallelogram,
                    tool_command="echo val=${{a.b.c}} > {output_file}"]
            exit [shape=Msquare]
            start -> worker -> exit
        }}
        """,
        logs_root=str(tmp_path),
    )
    engine.context.set("a.b.c", "deep_value")

    await engine.run()

    assert engine.node_outcomes["worker"].status == StageStatus.SUCCESS
    content = output_file.read_text()
    assert "deep_value" in content, (
        f"Expected ${{a.b.c}} substituted with 'deep_value', got: {content!r}"
    )
    assert "${" not in content


@pytest.mark.asyncio
async def test_dotted_key_bare_form_substitution(tmp_path):
    """Design assertion #3: $a.b.c (bare form, with dots) resolves in tool_command.

    M5: The old `if '.' not in str(key)` guard is dropped.
    """
    output_file = tmp_path / "bare_dotted.txt"
    engine = _make_engine(
        f"""
        digraph {{
            start [shape=Mdiamond]
            worker [shape=parallelogram,
                    tool_command="echo tool_out=$tool.output > {output_file}"]
            exit [shape=Msquare]
            start -> worker -> exit
        }}
        """,
        logs_root=str(tmp_path),
    )
    engine.context.set("tool.output", "previous_tool_value")

    await engine.run()

    assert engine.node_outcomes["worker"].status == StageStatus.SUCCESS
    content = output_file.read_text()
    assert "previous_tool_value" in content, (
        f"Expected $tool.output substituted, got: {content!r}"
    )
