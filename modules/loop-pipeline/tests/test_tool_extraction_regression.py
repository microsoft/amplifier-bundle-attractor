"""Regression tests for Anthropic tool-based structured-output extraction.

Live-found bug: when Anthropic returns structured output via the synthetic
``__structured_output__`` tool (instead of as plain text), ``result.text`` is
empty.  The pipeline backend must recover the JSON from
``result.tool_calls[0].arguments`` in that case.

These tests lock in the recovery logic in BOTH backend paths:
  1. AmplifierBackend._run_with_tool_loop (backend.py)
  2. DirectProviderBackend.run (in __init__.py) — the EXT-23 structured-output
     branch that also reads result.tool_calls when result.text is empty.

All LLM calls are mocked — no live API keys required.
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Dependency stubs (amplifier_foundation may not be installed)
# ---------------------------------------------------------------------------

if "amplifier_foundation" not in sys.modules:

    @dataclass
    class _StubProviderPreference:
        provider: str = ""
        model: str = ""

    _stub_foundation = types.ModuleType("amplifier_foundation")
    _stub_foundation.ProviderPreference = _StubProviderPreference  # type: ignore[attr-defined]
    sys.modules["amplifier_foundation"] = _stub_foundation

unified_llm = pytest.importorskip("unified_llm")

from amplifier_module_loop_pipeline import DirectProviderBackend  # noqa: E402
from amplifier_module_loop_pipeline.backend import AmplifierBackend  # noqa: E402
from amplifier_module_loop_pipeline.context import PipelineContext  # noqa: E402
from amplifier_module_loop_pipeline.graph import Node  # noqa: E402
from amplifier_module_loop_pipeline.outcome import StageStatus  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}
_STRUCT_TOOL_NAME = "__structured_output__"
_EXPECTED_ARGS = {"answer": "Paris"}


def _make_node(node_id: str = "extract") -> Node:
    """Return a Node with response_schema set (triggers EXT-23 path)."""
    return Node(
        id=node_id,
        prompt="Extract structured info",
        llm_model="claude-sonnet-4-20250514",
        response_schema=_SCHEMA,
    )


def _make_tool_call(args: dict[str, Any]) -> "unified_llm.types.ToolCall":
    """Build a ToolCall for the synthetic __structured_output__ tool."""
    return unified_llm.ToolCall(
        id="call_struct_01",
        name=_STRUCT_TOOL_NAME,
        arguments=args,
    )


def _make_generate_result(
    *,
    text: str = "",
    tool_args: dict[str, Any] | None = None,
) -> "unified_llm.GenerateResult":  # type: ignore[name-defined]
    """Build a GenerateResult with optional __structured_output__ tool call.

    When ``text`` is empty and ``tool_args`` is provided, the result simulates
    the Anthropic structured-output path where the answer lives in the tool
    call arguments rather than result.text.
    """
    from unified_llm.types import StepResult

    tool_calls = []
    if tool_args is not None:
        tool_calls.append(_make_tool_call(tool_args))

    response = unified_llm.Response(
        id="r1",
        model="claude-sonnet-4-20250514",
        provider="anthropic",
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="tool_calls" if tool_args else "stop"),
        usage=unified_llm.Usage(input_tokens=5, output_tokens=10, total_tokens=15),
    )
    step = StepResult(
        text=text,
        tool_calls=[t for t in tool_calls],
        tool_results=[],
        finish_reason=response.finish_reason,
        usage=response.usage,
        response=response,
        warnings=[],
    )
    return unified_llm.GenerateResult(
        text=text,
        finish_reason=response.finish_reason,
        usage=response.usage,
        total_usage=unified_llm.Usage(input_tokens=5, output_tokens=10, total_tokens=15),
        steps=[step],
        response=response,
        tool_calls=tool_calls,
    )


# Default injected client for the helpers below.  All tests in this file
# patch unified_llm.generate directly (via `patch("unified_llm.generate", ...)`),
# so the client's identity is never used -- it only needs to be non-None so
# _get_or_create_unified_client() never falls through to
# unified_llm.Client.from_env(), which requires a real API key and would make
# these tests non-hermetic.
_DEFAULT_UNIFIED_CLIENT_STUB = object()


def _make_amplifier_backend(
    unified_client: Any = _DEFAULT_UNIFIED_CLIENT_STUB,
) -> AmplifierBackend:
    """Create an AmplifierBackend with spawn disabled (falls to tool loop)."""
    return AmplifierBackend(
        coordinator=MagicMock(
            get_capability=lambda _: None,
            config={"agents": {}},
            session=MagicMock(),
        ),
        profiles={},
        provider=MagicMock(),
        unified_client=unified_client,
    )


def _make_direct_backend(
    unified_client: Any = _DEFAULT_UNIFIED_CLIENT_STUB,
) -> DirectProviderBackend:
    """Create a DirectProviderBackend wrapping the given mock client."""
    return DirectProviderBackend(
        provider=MagicMock(),
        tools={},
        hooks=None,
        coordinator=None,
        unified_client=unified_client,
    )


# ---------------------------------------------------------------------------
# AmplifierBackend._run_with_tool_loop regression tests
# ---------------------------------------------------------------------------


class TestAmplifierBackendToolExtraction:
    """Regression: AmplifierBackend._run_with_tool_loop recovers structured output
    from result.tool_calls when result.text is empty (Anthropic path)."""

    @pytest.mark.asyncio
    async def test_extracts_from_tool_call_when_text_is_empty(self) -> None:
        """When result.text='' and a __structured_output__ tool call exists,
        the outcome notes and context_updates are populated from the arguments."""
        node = _make_node()
        mock_result = _make_generate_result(text="", tool_args=_EXPECTED_ARGS)

        with patch("unified_llm.generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = mock_result
            backend = _make_amplifier_backend()
            outcome = await backend._run_with_tool_loop(
                node=node,
                instruction="Extract the thing",
                reasoning_effort=None,
            )

        assert outcome.status == StageStatus.SUCCESS
        # Notes should contain the JSON-serialized arguments
        assert outcome.notes is not None
        assert "answer" in outcome.notes
        parsed = json.loads(outcome.notes)
        assert parsed == _EXPECTED_ARGS
        # context_updates[node.id] should be the parsed dict
        assert outcome.context_updates is not None
        assert outcome.context_updates[node.id] == _EXPECTED_ARGS

    @pytest.mark.asyncio
    async def test_no_tool_call_with_empty_text_gives_empty_notes(self) -> None:
        """When result.text='' and NO __structured_output__ tool call exists,
        context_updates[node.id] is absent (no data to populate)."""
        node = _make_node()
        mock_result = _make_generate_result(text="", tool_args=None)

        with patch("unified_llm.generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = mock_result
            backend = _make_amplifier_backend()
            outcome = await backend._run_with_tool_loop(
                node=node,
                instruction="Extract the thing",
                reasoning_effort=None,
            )

        # Empty text, no tool call → no parsed_obj → context_updates has no node.id key
        assert outcome.status == StageStatus.SUCCESS
        assert outcome.context_updates is not None
        assert node.id not in outcome.context_updates

    @pytest.mark.asyncio
    async def test_ignores_unrelated_tool_calls(self) -> None:
        """Tool calls with a name other than __structured_output__ are not used
        for structured-output recovery."""
        from unified_llm.types import ToolCall

        node = _make_node()
        unrelated_tc = ToolCall(id="c1", name="report_outcome", arguments={"status": "success"})

        response = unified_llm.Response(
            id="r1",
            model="claude-sonnet-4-20250514",
            provider="anthropic",
            message=unified_llm.Message.assistant(""),
            finish_reason=unified_llm.FinishReason(reason="tool_calls"),
            usage=unified_llm.Usage(input_tokens=5, output_tokens=10, total_tokens=15),
        )
        from unified_llm.types import StepResult

        mock_result = unified_llm.GenerateResult(
            text="",
            finish_reason=response.finish_reason,
            usage=response.usage,
            total_usage=response.usage,
            steps=[
                StepResult(
                    text="",
                    tool_calls=[unrelated_tc],
                    tool_results=[],
                    finish_reason=response.finish_reason,
                    usage=response.usage,
                    response=response,
                    warnings=[],
                )
            ],
            response=response,
            tool_calls=[unrelated_tc],
        )

        with patch("unified_llm.generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = mock_result
            backend = _make_amplifier_backend()
            outcome = await backend._run_with_tool_loop(
                node=node,
                instruction="Extract the thing",
                reasoning_effort=None,
            )

        # The outcome succeeds but node.id is not in context_updates
        # because the tool call was not __structured_output__
        assert outcome.status == StageStatus.SUCCESS
        if outcome.context_updates:
            assert node.id not in outcome.context_updates


# ---------------------------------------------------------------------------
# DirectProviderBackend.run regression tests
# ---------------------------------------------------------------------------


class TestDirectBackendToolExtraction:
    """Regression: DirectProviderBackend.run EXT-23 path recovers structured output
    from result.tool_calls when result.text is empty (Anthropic path)."""

    @pytest.mark.asyncio
    async def test_extracts_from_tool_call_when_text_is_empty(self) -> None:
        """When result.text='' and a __structured_output__ tool call exists,
        the outcome notes and context_updates are populated from the arguments."""
        node = _make_node()
        context = PipelineContext()
        mock_result = _make_generate_result(text="", tool_args=_EXPECTED_ARGS)

        with patch("unified_llm.generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = mock_result
            backend = _make_direct_backend()
            outcome = await backend.run(
                node=node,
                prompt="Extract the thing",
                context=context,
            )

        assert outcome.status == StageStatus.SUCCESS
        assert outcome.notes is not None
        assert "answer" in outcome.notes
        parsed = json.loads(outcome.notes)
        assert parsed == _EXPECTED_ARGS
        # context_updates[node.id] should be the parsed dict
        assert outcome.context_updates is not None
        assert outcome.context_updates[node.id] == _EXPECTED_ARGS

    @pytest.mark.asyncio
    async def test_tool_extraction_only_fires_on_schema_nodes(self) -> None:
        """The __structured_output__ recovery only fires when node.response_schema
        is set.  Without a schema, the tool call is treated normally (ignored
        or handled as a plain tool call)."""
        # A node WITHOUT response_schema
        plain_node = Node(
            id="plain",
            prompt="Do the work",
            llm_model="claude-sonnet-4-20250514",
            response_schema=None,
        )
        context = PipelineContext()
        # Even if the result has a tool call, without response_schema the code
        # takes the non-EXT-23 path
        mock_result = _make_generate_result(text="", tool_args=_EXPECTED_ARGS)

        with patch("unified_llm.generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = mock_result
            backend = _make_direct_backend()
            outcome = await backend.run(
                node=plain_node,
                prompt="Do the work",
                context=context,
            )

        # Without response_schema, the tool call is NOT used as structured output.
        # The outcome succeeds (empty text → SUCCESS per spec 4.5).
        assert outcome.status == StageStatus.SUCCESS
        # context_updates[node.id] must NOT be the extracted args
        if outcome.context_updates:
            assert outcome.context_updates.get(plain_node.id) != _EXPECTED_ARGS
