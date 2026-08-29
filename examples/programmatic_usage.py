#!/usr/bin/env python3
"""Programmatic usage of the Attractor pipeline engine.

Two modes of operation:

  Option A: AmplifierBackend's `direct` worker (no Amplifier session)
    - Just LLM calls via unified_llm. No tools.
    - Good for analysis, reasoning, and writing pipelines.
    - Requirements: pip install amplifier-module-loop-pipeline unified-llm-client

  Option B: Full AmplifierSession with session.spawn
    - Each pipeline node gets a full sub-session with tools.
    - Good for coding pipelines (file edits, shell commands).
    - Requirements: pip install amplifier-foundation

Environment:
    Set at least one provider API key:
    - ANTHROPIC_API_KEY
    - OPENAI_API_KEY
    - GEMINI_API_KEY
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Example DOT pipelines
# ---------------------------------------------------------------------------

ANALYSIS_PIPELINE = r"""
digraph {
    graph [goal="Explain the trade-offs between microservices and monoliths"]

    start     [shape=Mdiamond]
    research  [prompt="List the key trade-offs for: $goal", llm_provider="anthropic"]
    synthesis [prompt="Synthesize the research into a concise 3-paragraph summary"]
    done      [shape=Msquare]

    start -> research -> synthesis -> done
}
"""

CODING_PIPELINE = r"""
digraph {
    graph [goal="Create a Python function that checks if a number is prime"]

    start     [shape=Mdiamond]
    implement [prompt="$goal. Write it to prime.py with type hints and docstring.", goal_gate=true]
    test      [prompt="Write pytest tests for prime.py and run them."]
    done      [shape=Msquare]

    start -> implement -> test -> done
}
"""


# ===================================================================
# OPTION A: Direct LLM calls (no Amplifier session, no tools)
# ===================================================================


async def run_direct(dot_source: str) -> None:
    """Run a pipeline using AmplifierBackend's `direct` worker.

    This is the simplest integration. No Amplifier session, no tools.
    Each pipeline node makes a direct LLM call via unified_llm.
    """
    import unified_llm
    from amplifier_module_loop_pipeline.backend import AmplifierBackend
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.engine import PipelineEngine
    from amplifier_module_loop_pipeline.handlers import HandlerRegistry
    from amplifier_module_loop_pipeline.transforms import apply_transforms
    from amplifier_module_loop_pipeline.validation import validate_or_raise

    # Parse, transform, validate
    graph = parse_dot(dot_source)
    context = PipelineContext()
    apply_transforms(graph, context)
    validate_or_raise(graph)

    try:
        # unified_llm.Client.from_env() reads ANTHROPIC_API_KEY / OPENAI_API_KEY
        # / GEMINI_API_KEY (GOOGLE_API_KEY accepted as a Gemini alias) and
        # raises ConfigurationError if none is set.
        client = unified_llm.Client.from_env()
    except unified_llm.ConfigurationError:
        print(
            "No provider API key set (ANTHROPIC_API_KEY / OPENAI_API_KEY / "
            "GEMINI_API_KEY) -- skipping the direct-call example."
        )
        return

    # No coordinator -> the worker registry's `direct` worker handles every
    # node (specs/EXTENSIONS.md Sec40 in amplifier-bundle-dot-runner).
    # `provider=` and `unified_client=` both take the SAME client: `provider`
    # is only a truthiness flag that enables the `direct` worker's dispatch
    # branch, `unified_client` is what actually makes the LLM calls (mirrors
    # amplifier-bundle-dot-runner's pipeline-runner
    # `_bootstrap_direct_provider()` reference pattern).
    backend = AmplifierBackend(
        provider=client, unified_client=client, default_worker="direct"
    )
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=HandlerRegistry(backend=backend),
        logs_root=tempfile.mkdtemp(prefix="attractor-"),
    )

    outcome = await engine.run()
    print(f"Status: {outcome.status.value}")
    if outcome.notes:
        print(f"Result: {outcome.notes[:500]}")


# ===================================================================
# OPTION B: Full Amplifier session with tools per node
# ===================================================================

ATTRACTOR_BUNDLE = (
    "git+https://github.com/microsoft/amplifier-bundle-attractor@main"
    "#subdirectory=profiles/attractor-profile-anthropic"
)


def register_spawn_capability(session: Any, prepared: Any) -> None:
    """Register session.spawn so pipeline nodes get full sub-sessions.

    This is the minimal implementation. For production use, see
    amplifier-foundation/examples/07_full_workflow.py which handles
    additional kwargs (tool_inheritance, hook_inheritance, etc.).

    Agent configs are inline bundle overlays -- dicts of bundle fields
    (``{"session": {...}, "providers": [...], "tools": [...], ...}``).
    The child ``Bundle(...)`` is built directly from those fields.

    Note: hooks composed into the PARENT bundle auto-propagate to every
    spawned child via ``prepared.spawn(compose=True)`` (the default).
    """
    from amplifier_foundation import Bundle
    from amplifier_foundation.bundle import PreparedBundle

    assert isinstance(prepared, PreparedBundle)

    async def spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: Any,
        agent_configs: dict[str, dict[str, Any]],
        sub_session_id: str | None = None,
        orchestrator_config: dict[str, Any] | None = None,
        parent_messages: list[dict[str, Any]] | None = None,
        provider_preferences: list | None = None,
        self_delegation_depth: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        # Resolve agent name to config
        if agent_name in agent_configs:
            config = agent_configs[agent_name]
        elif agent_name in prepared.bundle.agents:
            config = prepared.bundle.agents[agent_name]
        else:
            available = list(agent_configs.keys()) + list(prepared.bundle.agents.keys())
            raise ValueError(f"Agent '{agent_name}' not found. Available: {available}")

        # Build child Bundle from inline agent overlay.
        child_bundle = Bundle(
            name=agent_name,
            version="1.0.0",
            session=config.get("session", {}),
            providers=config.get("providers", []),
            tools=config.get("tools", []),
            hooks=config.get("hooks", []),
            instruction=config.get("instruction")
            or config.get("system", {}).get("instruction"),
        )

        return await prepared.spawn(
            child_bundle=child_bundle,
            instruction=instruction,
            session_id=sub_session_id,
            parent_session=parent_session,
            orchestrator_config=orchestrator_config,
            parent_messages=parent_messages,
            provider_preferences=provider_preferences,
            self_delegation_depth=self_delegation_depth,
        )

    session.coordinator.register_capability("session.spawn", spawn_capability)


async def run_with_session(dot_source: str) -> None:
    """Run a pipeline with full Amplifier sessions and tools per node."""
    from amplifier_foundation import Bundle, load_bundle

    # Load attractor profile, overlay with our DOT source
    bundle = await load_bundle(ATTRACTOR_BUNDLE)
    overlay = Bundle(
        name="programmatic-run",
        session={
            "orchestrator": {
                "module": "loop-pipeline",
                "config": {"dot_source": dot_source},
            }
        },
    )
    composed = bundle.compose(overlay)

    prepared = await composed.prepare()
    session = await prepared.create_session(session_cwd=Path.cwd())
    register_spawn_capability(session, prepared)

    async with session:
        result = await session.execute("Run the pipeline")
        print(result)


# ===================================================================
# Main
# ===================================================================

if __name__ == "__main__":
    import sys

    if "--session" in sys.argv:
        print("Running with full Amplifier session (Option B)...")
        asyncio.run(run_with_session(CODING_PIPELINE))
    else:
        print("Running with direct LLM calls (Option A)...")
        print("(Use --session for full Amplifier session with tools)")
        asyncio.run(run_direct(ANALYSIS_PIPELINE))
