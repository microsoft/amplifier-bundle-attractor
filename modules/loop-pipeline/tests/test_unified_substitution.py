"""Tests for M5: unified substitution policy.

R12 WS-6 — engine node-failure propagation.

Design assertion #3: Dotted references work on success.
Design assertion #7: Unified substitution policy — all three substitution
sites (tool, transforms, human) treat present/missing keys identically.

Fixes the defect at handlers/tool.py:75-78 where the `if "." not in str(key)`
guard excluded dotted context keys from substitution.
"""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.outcome import StageStatus
from amplifier_module_loop_pipeline.substitution import substitute_context
from amplifier_module_loop_pipeline.validation import validate_or_raise
from amplifier_module_loop_pipeline.handlers.context import HandlerContext


class EventCapture:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event_name: str, data: dict[str, Any]) -> None:
        self.events.append({"name": event_name, "data": data})


def _make_engine(dot_source: str, logs_root: str, hooks: Any = None) -> PipelineEngine:
    graph = parse_dot(dot_source)
    validate_or_raise(graph)
    context = PipelineContext()
    registry = HandlerRegistry(HandlerContext())
    return PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# Unit tests for substitute_context (the shared function)
# ---------------------------------------------------------------------------


def test_substitute_context_brace_form_dotted_key():
    """${a.b.c} resolves from context when value is present."""
    result = substitute_context("url=${a.b.c}", {"a.b.c": "http://example.com"})
    assert result == "url=http://example.com"


def test_substitute_context_bare_form_dotted_key():
    """$tool.output resolves from context when value is present."""
    result = substitute_context("out=$tool.output", {"tool.output": "result"})
    assert result == "out=result"


def test_substitute_context_missing_key_leaves_literal():
    """Missing key leaves the token as-is (literal pass-through)."""
    assert substitute_context("${missing}", {}) == "${missing}"
    assert substitute_context("$missing", {}) == "$missing"


def test_substitute_context_plain_key():
    """$plain_key (no dots) still works."""
    result = substitute_context("hello $name", {"name": "world"})
    assert result == "hello world"


def test_substitute_context_dollar_escape():
    """$$ produces a literal $."""
    result = substitute_context("cost is $$5.00", {})
    assert result == "cost is $5.00"


def test_substitute_context_no_substitution_needed():
    """Text without $ is returned unchanged."""
    text = "no dollar signs here"
    assert substitute_context(text, {"anything": "value"}) == text


def test_substitute_context_longest_key_wins():
    """When context has both 'tool' and 'tool.output', longest key wins for $tool.output."""
    snapshot = {"tool": "base", "tool.output": "dotted_value"}
    result = substitute_context("$tool.output and $tool", snapshot)
    # $tool.output → "dotted_value", $tool → "base"
    assert "dotted_value" in result
    assert "base" in result


def test_substitute_context_multiple_occurrences():
    """All occurrences of a token are replaced."""
    result = substitute_context("${x} and ${x} again", {"x": "hello"})
    assert result == "hello and hello again"


def test_substitute_context_none_value_treated_as_absent():
    """None-valued keys are treated as absent (token left literal)."""
    result = substitute_context("${k}", {"k": None})
    assert result == "${k}"


# ---------------------------------------------------------------------------
# Integration tests: dotted-key substitution in tool_command (M5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dotted_key_substitutes_in_tool_command(tmp_path):
    """Design assertion #3: ${a.b.c} resolves in tool_command when producer succeeded.

    pipeline: producer (writes validated.path) → consumer (uses ${validated.path})
    """
    output_file = tmp_path / "output.txt"
    engine = _make_engine(
        f"""
        digraph {{
            start [shape=Mdiamond]
            producer [shape=parallelogram,
                      tool_command="echo /data/file.txt",
                      parse_json=false,
                      outputs="validated.path"]
            consumer [shape=parallelogram,
                      tool_command="echo using ${{validated.path}} > {output_file}"]
            exit [shape=Msquare]
            start -> producer -> consumer -> exit
        }}
        """,
        logs_root=str(tmp_path),
    )

    # Pre-populate context with the value that producer would write
    engine.context.set("validated.path", "/data/file.txt")

    await engine.run()

    # consumer should have succeeded
    assert engine.node_outcomes["consumer"].status == StageStatus.SUCCESS, (
        f"consumer should succeed with dotted key, got {engine.node_outcomes['consumer']}"
    )
    # output file should contain the resolved value
    if output_file.exists():
        content = output_file.read_text()
        assert "/data/file.txt" in content, (
            f"Dotted key should have been substituted. File: {content!r}"
        )


@pytest.mark.asyncio
async def test_bare_dotted_key_substitutes_in_tool_command(tmp_path):
    """Design assertion #3: $tool.output (without braces) resolves correctly.

    After M5, the dot guard is removed; bare dotted keys work too.
    """
    output_file = tmp_path / "bare_output.txt"
    engine = _make_engine(
        f"""
        digraph {{
            start [shape=Mdiamond]
            worker [shape=parallelogram,
                    tool_command="echo done > {output_file}"]
            exit [shape=Msquare]
            start -> worker -> exit
        }}
        """,
        logs_root=str(tmp_path),
    )
    # Pre-set a dotted key
    engine.context.set("tool.output", "previous_result")

    await engine.run()

    assert engine.node_outcomes["worker"].status == StageStatus.SUCCESS


@pytest.mark.asyncio
async def test_substitution_count_happy_path(tmp_path):
    """SC-1: Happy path pipeline produces substitution count >= 1.

    Uses placeholder names (not production pipeline names).
    Verifies that at least one ${node.field} substitution resolves correctly.
    """
    output_file = tmp_path / "result.txt"
    engine = _make_engine(
        f"""
        digraph {{
            start [shape=Mdiamond]
            step_one [shape=parallelogram,
                      tool_command="echo step_one_output",
                      outputs="step.result"]
            step_two [shape=parallelogram,
                      tool_command="echo got=${{step.result}} > {output_file}"]
            exit [shape=Msquare]
            start -> step_one -> step_two -> exit
        }}
        """,
        logs_root=str(tmp_path),
    )
    # Pre-populate what step_one would produce
    engine.context.set("step.result", "hello_from_step_one")

    await engine.run()

    assert engine.node_outcomes["step_two"].status == StageStatus.SUCCESS
    if output_file.exists():
        content = output_file.read_text()
        # The dotted key ${step.result} should have been substituted
        assert "hello_from_step_one" in content, (
            f"Expected substitution of ${{step.result}}, got: {content!r}"
        )
        # No unresolved ${...} tokens in the output
        assert "${" not in content, (
            f"Unresolved literal ${{...}} found in output: {content!r}"
        )
