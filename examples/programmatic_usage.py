#!/usr/bin/env python3
"""Programmatic usage of the Attractor pipeline engine.

Two modes of operation:

  Option A: DirectProviderBackend (no Amplifier session)
    - Just LLM calls via unified_llm. No tools.
    - Good for analysis, reasoning, and writing pipelines.
    - Requirements: pip install amplifier-module-loop-pipeline unified-llm-client

  Option B: Full AmplifierSession with session.spawn
    - Each pipeline node gets a full sub-session with tools.
    - Good for coding pipelines (file edits, shell commands).
    - Requirements: pip install amplifier-foundation

  Option C: DirectProviderBackend against a LOCAL model
    - Same as Option A, but served by an OpenAI-compatible endpoint you host
      (Ollama, vLLM, llama.cpp, LM Studio, Docker Model Runner).
    - Cost, and data control: context for these nodes never leaves your box.
    - Two ways in: environment variables, or an explicitly injected Client.

Environment:
    Set at least one provider API key:
    - ANTHROPIC_API_KEY
    - OPENAI_API_KEY
    - GEMINI_API_KEY

    ...or point at a local OpenAI-compatible endpoint (no key required):
    - OPENAI_COMPAT_BASE_URL=http://localhost:11434/v1
    - OPENAI_COMPAT_PROVIDER_NAME=local   (optional, default "local")
    - OPENAI_COMPAT_API_KEY=...           (optional; local servers ignore it)
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

# Note what this graph does NOT contain: a URL, a port, or a credential.
# `llm_provider="local"` is a ROLE; where that role lives is deployment config.
# That separation is what keeps a .dot portable across laptop / CI / GPU box.
# Use a CONCRETE model id -- globs and family tokens resolve against cloud
# catalogues and will not match a locally served model.
LOCAL_PIPELINE = r"""
digraph {
    graph [goal="Summarize the trade-offs of running LLMs locally"]

    start    [shape=Mdiamond]
    analyze  [prompt="$goal. Answer in three bullets.",
              llm_provider="local", llm_model="qwen2.5-coder:7b"]
    done     [shape=Msquare]

    start -> analyze -> done
}
"""


# ===================================================================
# OPTION A: Direct LLM calls (no Amplifier session, no tools)
# ===================================================================

async def run_direct(dot_source: str) -> None:
    """Run a pipeline using DirectProviderBackend.

    This is the simplest integration. No Amplifier session, no tools.
    Each pipeline node makes a direct LLM call via unified_llm.
    """
    from amplifier_module_loop_pipeline import DirectProviderBackend
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

    # provider=None -> auto-creates unified_llm.Client from env vars
    backend = DirectProviderBackend(provider=None)
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
# OPTION C: Direct LLM calls against a LOCAL OpenAI-compatible endpoint
# ===================================================================


async def run_local(dot_source: str) -> None:
    """Run a pipeline on a model you host yourself.

    Works with any OpenAI-compatible ``/v1/chat/completions`` server: Ollama,
    vLLM, llama.cpp, LM Studio, Docker Model Runner.

    Why: cost, and data control -- context for these nodes never leaves the
    machine serving the endpoint.

    Two ways to supply the endpoint. Both are equivalent; neither puts a URL
    in the .dot.

    1. ENVIRONMENT (nothing to write in code)::

           export OPENAI_COMPAT_BASE_URL=http://localhost:11434/v1
           export OPENAI_COMPAT_PROVIDER_NAME=local   # matches llm_provider=

       then just ``DirectProviderBackend(provider=object())`` -- it builds the
       client from the environment, exactly like Option A.

    2. INJECTED CLIENT (shown below) -- for apps that already hold their own
       config and do not want to route it through environment variables.
    """
    import unified_llm
    from amplifier_module_loop_pipeline import DirectProviderBackend
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.engine import PipelineEngine
    from amplifier_module_loop_pipeline.handlers import HandlerRegistry
    from amplifier_module_loop_pipeline.handlers.context import HandlerContext
    from amplifier_module_loop_pipeline.transforms import apply_transforms
    from amplifier_module_loop_pipeline.validation import validate_or_raise
    from unified_llm.adapters.openai_compat import OpenAICompatAdapter

    # The registry key ("local") is what llm_provider= in the DOT resolves
    # against. Pass the SAME string as name= -- the adapter reports it in
    # Response.provider, so a mismatch makes your audit trail claim the data
    # went somewhere it did not.
    client = unified_llm.Client(
        providers={
            "local": OpenAICompatAdapter(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key="not-needed",  # local servers ignore this
            )
        },
        default_provider="local",
    )

    graph = parse_dot(dot_source)
    context = PipelineContext()
    apply_transforms(graph, context)
    validate_or_raise(graph)

    backend = DirectProviderBackend(
        provider=object(),  # TRUTHINESS FLAG, not a provider -- see below
        unified_client=client,
        provider_names=("local",),
        default_provider="local",
    )
    # Foot-gun: `provider` here is only a flag enabling the direct path. Pass
    # None and the pipeline silently drops into simulation mode -- it "runs"
    # and produces nothing real.

    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=tempfile.mkdtemp(prefix="attractor-local-"),
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
        session={"orchestrator": {
            "module": "loop-pipeline",
            "config": {"dot_source": dot_source},
        }},
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