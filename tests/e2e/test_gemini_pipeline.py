"""E2E test for a Gemini-based pipeline via the Python API (AmplifierBackend's llm-direct worker).

Unlike the CLI-based approach, this test drives loop-pipeline directly through
the Python API, avoiding workspace settings interference and remote module
loading issues that break CLI-based pipeline tests.

Requires GOOGLE_API_KEY and loop-pipeline installed in the Python environment.
The modules are installed into the amplifier tool env:

    uv pip install -e ./modules/loop-pipeline -e ./modules/unified-llm-client

Run with:

    uv run pytest tests/e2e/test_gemini_pipeline.py -v --timeout=600
"""

import asyncio
import importlib.util
import os
import tempfile

import pytest

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
HAS_PIPELINE = importlib.util.find_spec("amplifier_module_loop_pipeline") is not None

pytestmark = [
    pytest.mark.skipif(not GOOGLE_API_KEY, reason="GOOGLE_API_KEY not set"),
    pytest.mark.skipif(not HAS_PIPELINE, reason="loop-pipeline not installed"),
]

PIPELINE_TIMEOUT = 600  # seconds

# Inline DOT: simple single-node pipeline using Gemini.
# No tools registered — AmplifierBackend's llm-direct worker has tools={}.
# goal_gate is intentionally omitted: goal_gate=true requires a report_outcome
# tool call that agents make; the llm-direct worker just runs LLM text generation.
# The model generates a text response and the pipeline completes with SUCCESS.
DOT_GEMINI_SIMPLE = r"""
digraph simple_gemini_test {
    graph [goal="Describe a hello world Python script"]

    start     [shape=Mdiamond]
    implement [shape=box, prompt="Write a brief description of what a hello world Python script looks like. No tools needed, just respond with text.", llm_provider="gemini", llm_model="gemini-2.5-pro"]
    done      [shape=Msquare]

    start -> implement -> done
}
"""


async def _run_pipeline(dot_source: str) -> object:
    """Run a DOT pipeline via the Python API and return the final Outcome."""
    # Imports are inside the function so pyright doesn't see them as possibly
    # unbound, and because this function is only called when HAS_PIPELINE=True.
    import unified_llm  # type: ignore[import-untyped]
    from amplifier_module_loop_pipeline.backend import (
        AmplifierBackend,  # type: ignore[import-untyped]
    )
    from amplifier_module_loop_pipeline.context import (
        PipelineContext,  # type: ignore[import-untyped]
    )
    from amplifier_module_loop_pipeline.dot_parser import (
        parse_dot,  # type: ignore[import-untyped]
    )
    from amplifier_module_loop_pipeline.engine import (
        PipelineEngine,  # type: ignore[import-untyped]
    )
    from amplifier_module_loop_pipeline.handlers import (
        HandlerRegistry,  # type: ignore[import-untyped]
    )
    from amplifier_module_loop_pipeline.transforms import (
        apply_transforms,  # type: ignore[import-untyped]
    )
    from amplifier_module_loop_pipeline.validation import (
        validate_or_raise,  # type: ignore[import-untyped]
    )

    graph = parse_dot(dot_source)
    context = PipelineContext()
    apply_transforms(graph, context)
    validate_or_raise(graph)

    # unified_llm.Client.from_env() picks up GOOGLE_API_KEY; no coordinator means
    # the `llm-direct` worker handles every node (EXTENSIONS.md Sec40).
    client = unified_llm.Client.from_env()
    backend = AmplifierBackend(
        provider=client,
        tools={},
        hooks=None,
        unified_client=client,
        default_worker="llm-direct",
    )
    logs_root = tempfile.mkdtemp(prefix="pipeline-gemini-e2e-")
    registry = HandlerRegistry(backend=backend)
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
    )
    return await engine.run()


def test_gemini_pipeline_simple(tmp_path):
    """Pipeline executes a single-node Gemini DOT graph and returns a success outcome.

    Graph: start -> implement -> done
    The implement node is assigned llm_provider="gemini" with goal_gate=true.
    AmplifierBackend's llm-direct worker drives the LLM call directly (no CLI, no workspace interference).
    """
    outcome = asyncio.run(_run_pipeline(DOT_GEMINI_SIMPLE))

    assert outcome.is_success, (  # type: ignore[union-attr]
        f"Pipeline did not complete successfully.\n"
        f"Status: {outcome.status!r}\n"  # type: ignore[union-attr]
        f"Notes: {outcome.notes!r}\n"  # type: ignore[union-attr]
        f"Failure reason: {outcome.failure_reason!r}"  # type: ignore[union-attr]
    )
